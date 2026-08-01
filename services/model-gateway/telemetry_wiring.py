"""model-gateway telemetry adoption (plan step 15; AC-03, AC-04; DE-5).

Mechanical, header/context-propagation-only adoption of telemetry_lib:
configure_tracer() is best-effort (a missing APPLICATIONINSIGHTS_
CONNECTION_STRING is the normal state absent a deployed App Insights
resource, and must never crash startup — the SAME discipline
orchestrator/telemetry_wiring.py already applies). emit_completion_span()
extracts the W3C traceparent header orchestrator/clients/gateway_client.py
already injects on every outbound call (DE-5) and opens ONE span per
POST /v1/completions request as a child of that trace, so this service's
own span joins the SAME trace_id as the orchestrator stage that called it.

No frozen contract is touched — the header travels outside the
CompletionRequest/CompletionResponse body entirely.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.propagate import extract
from opentelemetry.trace import Span, Tracer

logger = logging.getLogger("model-gateway")

SERVICE_NAME = "cmos-model-gateway"

# routing.yaml's own `version: 1` top-level field — a real, authentic
# registry_version value rather than a fabricated one.
ROUTING_POLICY_VERSION = "1"

_configured = False


def configure_tracer(connection_string: str | None = None) -> bool:
    global _configured
    if _configured:
        return True
    resolved = connection_string or os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING")
    if not resolved:
        return False
    try:
        from telemetry_lib import configure_tracer_provider

        configure_tracer_provider(resolved, service_name=SERVICE_NAME)
    except Exception as exc:  # noqa: BLE001 - telemetry must never crash startup
        logger.warning(
            json.dumps({"event": "telemetry_configure_failed", "error": str(exc)})
        )
        return False
    _configured = True
    return True


def get_tracer() -> Tracer:
    return trace.get_tracer(SERVICE_NAME)


@contextmanager
def emit_completion_span(
    headers: Any, *, model: str, task_ref: str | None, **optional_attrs: Any
) -> Iterator[Span | None]:
    """Opens one span per POST /v1/completions call, as a child of the
    caller's traceparent (if present) — main.py's create_completion route
    calls this around completion.handle_completion.

    The authoritative cost for a completion is metered directly to the
    Vault costs table by completion.py's own metering.record_completion_
    costs (unchanged) — this span's `cost` attribute is best-effort/
    decorative (0.0), matching the same honest-placeholder convention
    orchestrator/dispatch.py's non-LLM draft-brief span already uses.

    CRITICAL: telemetry must NEVER prevent the wrapped completion call
    from running, and must NEVER swallow/alter an exception the wrapped
    call raises (an oversized/malformed `model`/`task_ref` string
    tripping telemetry_lib's own free-text length guard must degrade to a
    no-op span, not abort the request before the `yield`; a real error
    from the wrapped completion call must propagate through this
    contextmanager completely unchanged — a `try/except` wrapped AROUND
    the `yield` itself would incorrectly intercept the wrapped call's own
    exceptions here, so span SETUP is isolated into its own try/except
    that never touches the yield).
    """
    from telemetry_lib import start_span

    # start_span's underlying tracer.start_as_current_span() has no
    # `context=` parameter of its own -- attaching the extracted parent to
    # the ambient OTel context first is what makes it pick up the
    # caller's trace_id as its parent automatically (same mechanism as
    # orchestrator/telemetry_wiring.emit_task_span's run_id handling).
    try:
        parent_context = extract(headers or {})
        token = otel_context.attach(parent_context)
    except Exception:  # noqa: BLE001 - telemetry must never block a real request
        token = None

    span_cm = None
    span: Span | None = None
    try:
        span_cm = start_span(
            get_tracer(),
            "model-gateway.completions",
            function_id="model-gateway.completions",
            task_ref=task_ref or "unknown",
            model=model,
            registry_version=ROUTING_POLICY_VERSION,
            cost=0.0,
            **optional_attrs,
        )
        span = span_cm.__enter__()
    except Exception as exc:  # noqa: BLE001 - span SETUP only; never the wrapped call
        logger.warning(json.dumps({"event": "telemetry_span_setup_failed", "error": str(exc)}))
        span_cm = None
        span = None

    try:
        yield span
    finally:
        if span_cm is not None:
            import sys

            span_cm.__exit__(*sys.exc_info())
        if token is not None:
            otel_context.detach(token)
