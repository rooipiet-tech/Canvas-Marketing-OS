"""Shared telemetry adoption for mcp-web / mcp-buffer / mcp-canva (plan
steps 15-16; AC-03, AC-04; DE-5).

One implementation, shared via mcp_common (all 3 servers already import
mcp_common.protocol.MCPServer), rather than 3 near-identical copies —
unlike model-gateway/gatekeeper/publisher/vault (4 independently-deployed
services that deliberately share no library), the 3 mcp-* servers already
share this exact package.

configure_tracer() is best-effort and never crashes startup.
open_tool_call_span()/close_tool_call_span() extract the W3C traceparent
header a caller (orchestrator/clients/mcp_client.py, or Publisher's
buffer_client.py) already injects and open/close a span as a child of
that trace around one MCP tools/call — protocol.py's MCPServer.build_router
wires this into the shared POST /mcp route, so mcp-web/mcp-buffer/
mcp-canva all get it automatically.
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

logger = logging.getLogger("mcp_common.telemetry")

_configured_services: set[str] = set()


def configure_tracer(service_name: str, connection_string: str | None = None) -> bool:
    if service_name in _configured_services:
        return True
    resolved = connection_string or os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING")
    if not resolved:
        return False
    try:
        from telemetry_lib import configure_tracer_provider

        configure_tracer_provider(resolved, service_name=service_name)
    except Exception as exc:  # noqa: BLE001 - telemetry must never crash startup
        logger.warning(json.dumps({"event": "telemetry_configure_failed", "error": str(exc)}))
        return False
    _configured_services.add(service_name)
    return True


def get_tracer(service_name: str) -> Tracer:
    return trace.get_tracer(service_name)


def open_tool_call_span(
    service_name: str, headers: Any, *, tool_name: str
) -> tuple[Any, Any, Span | None]:
    """Extracts traceparent + opens a span, returning (span_cm,
    otel_context_token, span) for close_tool_call_span() to finish. Any
    setup failure degrades to (None, None, None) -- never raises, never
    blocks the real tool call that's about to run."""
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
            get_tracer(service_name),
            f"{service_name}.{tool_name}",
            function_id=tool_name,
            task_ref="unknown",
            model="none",
            registry_version="1",
            cost=0.0,
        )
        span = span_cm.__enter__()
    except Exception as exc:  # noqa: BLE001 - span SETUP only; never the wrapped call
        logger.warning(json.dumps({"event": "telemetry_span_setup_failed", "error": str(exc)}))
        span_cm = None
        span = None

    return span_cm, token, span


def close_tool_call_span(span_cm: Any, token: Any) -> None:
    """Closes what open_tool_call_span() opened. Called from a `finally`
    block so it runs whether or not the wrapped call raised."""
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
