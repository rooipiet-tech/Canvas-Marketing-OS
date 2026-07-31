"""Gatekeeper telemetry adoption (plan step 15; AC-03, AC-04; DE-5).

Mechanical, header/context-propagation-only adoption of telemetry_lib —
mirrors services/model-gateway/telemetry_wiring.py's structure exactly
(a separate, independent implementation; Gatekeeper shares no library
with model-gateway). configure_tracer() is best-effort and never crashes
startup. emit_gate_check_span() extracts the W3C traceparent header
orchestrator/clients/gatekeeper_client.py already injects and opens one
span per POST /gate-check call as a child of that trace.

Span setup (header extraction, span creation) is isolated into its own
try/except that degrades to a no-op span on any failure — structurally
separate from the wrapped call itself, so a telemetry-side error can
never alter or swallow an exception the real /gate-check handling raises
(see model-gateway/telemetry_wiring.py's docstring for the exact bug
class this avoids).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.propagate import extract
from opentelemetry.trace import Span, Tracer

logger = logging.getLogger("gatekeeper")

SERVICE_NAME = "cmos-gatekeeper"

_configured = False


def configure_tracer(connection_string: str | None = None) -> bool:
    global _configured
    if _configured:
        return True
    import os

    resolved = connection_string or os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING")
    if not resolved:
        return False
    try:
        from telemetry_lib import configure_tracer_provider

        configure_tracer_provider(resolved, service_name=SERVICE_NAME)
    except Exception as exc:  # noqa: BLE001 - telemetry must never crash startup
        logger.warning(json.dumps({"event": "telemetry_configure_failed", "error": str(exc)}))
        return False
    _configured = True
    return True


def get_tracer() -> Tracer:
    return trace.get_tracer(SERVICE_NAME)


@contextmanager
def emit_span(
    span_name: str,
    headers: Any,
    *,
    function_id: str,
    task_ref: str,
    registry_version: str = "1",
    **optional_attrs: Any,
) -> Iterator[Span | None]:
    """Opens one span per request, as a child of the caller's traceparent
    (if present). Gate-check/gate-token issuance is never itself an LLM
    call, so model='none'/cost=0.0 (the same honest-placeholder
    convention orchestrator/dispatch.py's non-LLM spans already use).

    See model-gateway/telemetry_wiring.py's docstring for why span setup
    (this function's own header-extraction/span-creation work) is
    isolated from the wrapped call's own exception propagation.
    """
    from telemetry_lib import start_span

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
            span_name,
            function_id=function_id,
            task_ref=task_ref,
            model="none",
            registry_version=registry_version,
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
