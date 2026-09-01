"""mcp-web — MCP server: rate-limited, egress-allow-listed URL fetch.

Dual-mode gate: MCP_WEB_LIVE_MODE (non-secret flag; see app/tools.py's
docstring for the orchestrator-approved waiver this reflects). Every tool
call passes through a shared sliding-window rate limiter (AC-5) before
dispatch, and every call is logged to mcp_ops.tool_calls when DATABASE_URL
is configured — logging is best-effort and never fails the tool call
itself (a Postgres outage must not take down the fetch tool).
"""

from __future__ import annotations

import os
import time

from fastapi import FastAPI
from mcp_common import MCPServer, ToolDefinition, log_tool_call
from mcp_common.logging import get_connection

from app.rate_limiter import RateLimitExceeded, build_default_rate_limiter
from app.tools import AllowlistViolation, fetch_url, probe_url

SERVER_NAME = "mcp-web"
SERVER_VERSION = "0.1.0"

TOOLS = [
    ToolDefinition(
        name="fetch_url",
        description="Fetch a URL's content. Restricted to an egress allow-list; rate-limited.",
        inputSchema={
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    ),
    ToolDefinition(
        name="probe_url",
        description=(
            "Report a candidate source's SHAPE for the source-promotion review -- status, "
            "content type, feed/item counts, extractable text size and up to five item "
            "titles. Never returns the body. Gated by MCP_WEB_PROBE_ALLOWLIST, a separate "
            "allowance from the production fetch_url egress list."
        ),
        inputSchema={
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    ),
]

_rate_limiter = build_default_rate_limiter()


def _caller_identity() -> str:
    return os.environ.get("MCP_CALLER_IDENTITY", "unknown")


def _log_best_effort(tool_name: str, arguments: dict, latency_ms: float, outcome: str) -> None:
    try:
        # Checks a connection OUT of the shared bounded pool and back IN
        # when the `with` block exits — never opens/closes a raw socket
        # per call (RISK-1/PERF-2 fix). Any failure here (DATABASE_URL
        # unset, pool exhausted, DB unreachable) is swallowed: logging is
        # best-effort and must never fail the underlying tool call.
        with get_connection() as conn:
            log_tool_call(
                conn,
                server_name=SERVER_NAME,
                tool_name=tool_name,
                caller_identity=_caller_identity(),
                arguments=arguments,
                latency_ms=latency_ms,
                outcome=outcome,
            )
    except Exception:
        return


def dispatch(tool_name: str, arguments: dict) -> dict:
    start = time.monotonic()

    try:
        _rate_limiter.check()
    except RateLimitExceeded:
        _log_best_effort(tool_name, arguments, (time.monotonic() - start) * 1000, "rate_limited")
        raise

    try:
        if tool_name == "fetch_url":
            result = fetch_url(arguments)
        elif tool_name == "probe_url":
            result = probe_url(arguments)
        else:
            raise ValueError(f"unknown tool: {tool_name}")
    except AllowlistViolation:
        _log_best_effort(tool_name, arguments, (time.monotonic() - start) * 1000, "rejected")
        raise
    except Exception:
        _log_best_effort(tool_name, arguments, (time.monotonic() - start) * 1000, "error")
        raise
    else:
        _log_best_effort(tool_name, arguments, (time.monotonic() - start) * 1000, "success")
        return result


mcp_server = MCPServer(name=SERVER_NAME, version=SERVER_VERSION, tools=TOOLS, dispatch=dispatch)

app = FastAPI(title=SERVER_NAME)
mcp_server.mount(app)
