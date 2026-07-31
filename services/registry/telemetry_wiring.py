"""Registry telemetry adoption (plan step 16; AC-03, AC-04; DE-5).

eval_harness.py's --live path (run_function_live) is a CLI-invoked client
of model-gateway, not a server receiving requests — there is no incoming
HTTP request/traceparent to join, so this module only NEEDS the
outbound-client half of the pattern: emit_live_eval_span() opens one
standalone span per live-attempt call, injecting the resulting
traceparent onto the outbound request via inject_traceparent() so
model-gateway's own adopted span (services/model-gateway/telemetry_wiring.py)
can still join THIS span's trace, even though nothing upstream started it.

configure_tracer() is best-effort and never crashes the CLI.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager

from opentelemetry import trace
from opentelemetry.propagate import inject
from opentelemetry.trace import Span, Tracer

logger = logging.getLogger("registry.telemetry")

SERVICE_NAME = "cmos-registry"

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
    except Exception as exc:  # noqa: BLE001 - telemetry must never crash the CLI
        logger.warning(json.dumps({"event": "telemetry_configure_failed", "error": str(exc)}))
        return False
    _configured = True
    return True


def get_tracer() -> Tracer:
    return trace.get_tracer(SERVICE_NAME)


def inject_traceparent(headers: dict[str, str] | None = None) -> dict[str, str]:
    """Returns `headers` (or a fresh dict) with the current span context's
    W3C traceparent added -- used before the outbound live gateway call so
    model-gateway's own adopted span can join this trace."""
    carrier: dict[str, str] = dict(headers or {})
    inject(carrier)
    return carrier


@contextmanager
def emit_live_eval_span(
    *, function_id: str, task_ref: str, model: str, cost: float = 0.0
) -> Iterator[Span | None]:
    """Opens one standalone span per --live eval attempt (no parent trace
    to join -- this is a CLI-invoked client call, not a server handling an
    upstream request). Never blocks the real live-attempt call: span
    SETUP failures degrade to a no-op span (see model-gateway/
    telemetry_wiring.py's docstring for the exact bug class this
    isolation avoids)."""
    from telemetry_lib import start_span

    span_cm = None
    span: Span | None = None
    try:
        span_cm = start_span(
            get_tracer(),
            "registry.eval_harness.live",
            function_id=function_id,
            task_ref=task_ref,
            model=model,
            registry_version="1",
            cost=cost,
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
