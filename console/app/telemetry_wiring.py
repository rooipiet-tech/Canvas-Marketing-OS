"""Console telemetry adoption (plan step 16; AC-03, AC-04; DE-5).

Mechanical, header/context-propagation-only adoption of telemetry_lib —
mirrors services/vault/vault/telemetry_wiring.py's open/close-pair shape
(a separate, independent implementation; console shares no library with
any other service) since main.py wires this into a new async middleware
that must straddle `await call_next(request)`.

configure_tracer() is best-effort and never crashes startup.
open_request_span()/close_request_span() extract the W3C traceparent
header a caller may have injected and open/close a span as a child of
that trace around one HTTP request.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.propagate import extract
from opentelemetry.trace import Span, Tracer

logger = logging.getLogger("console.telemetry")

SERVICE_NAME = "cmos-console"

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
        logger.warning(json.dumps({"event": "telemetry_configure_failed", "error": str(exc)}))
        return False
    _configured = True
    return True


def get_tracer() -> Tracer:
    return trace.get_tracer(SERVICE_NAME)


def open_request_span(
    headers: Any, *, function_id: str, task_ref: str
) -> tuple[Any, Any, Span | None]:
    """Extracts traceparent + opens a span, returning (span_cm,
    otel_context_token, span) for close_request_span() to finish. Any
    setup failure degrades to (None, None, None) -- never raises, never
    blocks the real request that's about to run."""
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
            "console.request",
            function_id=function_id,
            task_ref=task_ref,
            model="none",
            registry_version="1",
            cost=0.0,
        )
        span = span_cm.__enter__()
    except Exception as exc:  # noqa: BLE001 - span SETUP only; never the wrapped request
        logger.warning(json.dumps({"event": "telemetry_span_setup_failed", "error": str(exc)}))
        span_cm = None
        span = None

    return span_cm, token, span


def close_request_span(span_cm: Any, token: Any) -> None:
    """Closes what open_request_span() opened."""
    if span_cm is not None:
        try:
            span_cm.__exit__(*sys.exc_info())
        except Exception as exc:  # noqa: BLE001 - closing telemetry must never mask a real error
            logger.warning(json.dumps({"event": "telemetry_span_close_failed", "error": str(exc)}))
    if token is not None:
        try:
            otel_context.detach(token)
        except Exception:  # noqa: BLE001
            pass
