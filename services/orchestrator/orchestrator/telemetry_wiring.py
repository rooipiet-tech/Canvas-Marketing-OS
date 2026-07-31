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
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.propagate import extract, inject
from opentelemetry.trace import Span, Tracer

from orchestrator import config
from orchestrator.logging_config import get_logger, log_event

logger = get_logger("telemetry_wiring")

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


@contextmanager
def emit_task_span(
    name: str,
    *,
    function_id: str,
    task_ref: str,
    model: str,
    cost: float = 0.0,
    registry_version: str = DEFAULT_REGISTRY_VERSION,
    **optional_attrs: Any,
) -> Iterator[Span]:
    """The one span-opening entry point dispatch.py's handlers use.

    Falls back to a real (but unexported, since no tracer provider was
    installed) span when telemetry isn't configured — start_span's
    required-attribute enforcement still runs either way, so a handler
    can't accidentally skip carrying the 5 required fields just because
    APPLICATIONINSIGHTS_CONNECTION_STRING happens to be unset in a given
    environment.
    """
    from telemetry_lib import start_span

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
