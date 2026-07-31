"""Orchestrator telemetry adoption skeleton (plan step 5; AC-03, AC-04;
DE-5).

configure_tracer() is called once at FastAPI startup (main.py's lifespan)
and is entirely best-effort: no APPLICATIONINSIGHTS_CONNECTION_STRING
configured is a completely normal/expected state (this sandbox, most local
dev, any environment before the Application Insights resource exists) and
must never crash startup — mirrors every other orchestrator.config-reading
call's "degrade gracefully" discipline (main.py's own docstring).

emit_task_span() is the ONE place a dispatch handler opens the span
telemetry_lib.attributes.start_span requires (the 5 REQUIRED_ATTRS:
function_id/task_ref/model/registry_version/cost) — dispatch.py's 5
handlers (step 6+) all go through this rather than calling
telemetry_lib.start_span directly, so the tracer-lookup and
"telemetry not configured" fallback logic lives in exactly one place.

Cross-service trace linking (AC-03's "every stage's span linked under one
trace id") uses STANDARD W3C traceparent propagation (DE-5) via
opentelemetry's own default TraceContextTextMapPropagator —
inject_traceparent()/extract_traceparent() are thin wrappers so
orchestrator/clients/*.py's outbound HTTP calls carry the header, and each
downstream service (steps 15-16) extracts it as the parent context for its
own span. No frozen contract is touched: the header travels outside any
request BODY schema entirely.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.propagate import extract, inject
from opentelemetry.trace import (
    NonRecordingSpan,
    Span,
    SpanContext,
    TraceFlags,
    Tracer,
    set_span_in_context,
)

from orchestrator import config
from orchestrator.logging_config import get_logger, log_event

logger = get_logger("telemetry_wiring")

# A fixed, arbitrary nonzero span id used as the synthetic "root" parent
# for every run-scoped trace context below (OTel forbids an all-zero
# span_id). It is never itself exported as a real span -- it exists only
# so every one of a run's separately-dispatched task spans (each handled
# by an independent asyncio.to_thread call, worker.py) attaches under the
# SAME trace_id rather than each minting its own fresh one.
_SYNTHETIC_ROOT_SPAN_ID = 0x0000000000000001

SERVICE_NAME = "cmos-orchestrator"

# registry_version is a required span attribute (telemetry_lib) but this
# session's dispatch handlers don't (yet) read a function package's own
# version field at runtime — "unversioned" is an honest, non-fabricated
# placeholder rather than guessing a number. Update this the moment
# dispatch.py has a real registry-version source to read.
DEFAULT_REGISTRY_VERSION = "unversioned"

_configured = False


def configure_tracer(connection_string: str | None = None) -> bool:
    """Best-effort tracer provider setup. Returns True if a real exporter
    was installed, False if telemetry stays a no-op (never raises)."""
    global _configured
    if _configured:
        return True
    resolved = connection_string or config.APPLICATIONINSIGHTS_CONNECTION_STRING
    if not resolved:
        log_event(
            logger,
            logging.INFO,
            "telemetry_not_configured",
            reason="APPLICATIONINSIGHTS_CONNECTION_STRING unset",
        )
        return False
    try:
        from telemetry_lib import configure_tracer_provider

        configure_tracer_provider(resolved, service_name=SERVICE_NAME)
    except Exception as exc:  # noqa: BLE001 - telemetry must never crash startup
        log_event(logger, logging.WARNING, "telemetry_configure_failed", error=str(exc))
        return False
    _configured = True
    return True


def get_tracer() -> Tracer:
    return trace.get_tracer(SERVICE_NAME)


def _run_trace_id(run_id: str) -> int:
    """Deterministically derives a 128-bit OTel trace_id from a run
    identifier (orchestrator/dispatch.py passes TaskEnvelope.campaign_id
    — already a uuid5 shared by every task decomposed from the same
    heartbeat/loop_id pair, worker.py's handle_heartbeat_message). A uuid
    IS 128 bits, exactly OTel's trace_id width, so its .int value is used
    directly. Falls back to a SHA-256-derived 128-bit int for any
    non-uuid-shaped run_id (defensive only — every real caller always
    passes a genuine uuid string). Never 0 -- OTel treats an all-zero
    trace_id as invalid."""
    try:
        trace_id = uuid.UUID(str(run_id)).int
    except ValueError:
        import hashlib

        trace_id = int.from_bytes(hashlib.sha256(str(run_id).encode()).digest()[:16], "big")
    return trace_id or 1


def _run_parent_context(run_id: str) -> otel_context.Context:
    """A non-recording parent SpanContext carrying `run_id`'s derived
    trace_id — attaching this to the ambient OTel context before opening
    a span makes that span (and anything it nests) join the SAME trace,
    even though each of a run's task dispatches is an independent
    asyncio.to_thread call with no shared in-process span tree otherwise
    (AC-03's "every stage's span linked under one trace id")."""
    span_context = SpanContext(
        trace_id=_run_trace_id(run_id),
        span_id=_SYNTHETIC_ROOT_SPAN_ID,
        is_remote=True,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
    )
    return set_span_in_context(NonRecordingSpan(span_context))


@contextmanager
def emit_task_span(
    name: str,
    *,
    function_id: str,
    task_ref: str,
    model: str,
    cost: float = 0.0,
    registry_version: str = DEFAULT_REGISTRY_VERSION,
    run_id: str | None = None,
    **optional_attrs: Any,
) -> Iterator[Span]:
    """The one span-opening entry point dispatch.py's handlers use.

    `run_id` (dispatch.py passes envelope.campaign_id) links this span
    under a shared per-run trace_id (AC-03) — every downstream HTTP call
    made from within this `with` block also carries the resulting
    traceparent (inject_traceparent(), used by orchestrator/clients/*.py),
    so an adopting downstream service's own span (steps 15-16) joins the
    SAME trace too.

    Falls back to a real (but unexported, since no tracer provider was
    installed) span when telemetry isn't configured — start_span's
    required-attribute enforcement still runs either way, so a handler
    can't accidentally skip carrying the 5 required fields just because
    APPLICATIONINSIGHTS_CONNECTION_STRING happens to be unset in a given
    environment.
    """
    from telemetry_lib import start_span

    token = otel_context.attach(_run_parent_context(run_id)) if run_id else None
    try:
        with start_span(
            get_tracer(),
            name,
            function_id=function_id,
            task_ref=task_ref,
            model=model,
            registry_version=registry_version,
            cost=cost,
            **optional_attrs,
        ) as span:
            yield span
    finally:
        if token is not None:
            otel_context.detach(token)


def inject_traceparent(headers: dict[str, str] | None = None) -> dict[str, str]:
    """Return `headers` (or a fresh dict) with the current span context's
    W3C traceparent added — used by orchestrator/clients/*.py before every
    outbound HTTP call so the callee can join the same trace (DE-5)."""
    carrier: dict[str, str] = dict(headers or {})
    inject(carrier)
    return carrier


def extract_traceparent(headers: Any) -> Any:
    """Server side of the same propagation — returns an OTel Context built
    from an incoming request's headers (a plain dict or anything
    Mapping-like); pass it as `context=` to `tracer.start_as_current_span`.
    Used by steps 15-16's per-service adoption, not by the orchestrator
    itself (which only ever originates spans in this session's design)."""
    return extract(headers)
