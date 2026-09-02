"""mcp-buffer — MCP server: Buffer read-queue + create-draft allow-list.

Exactly 3 tools (AC-2): list_queue, get_post, create_draft. No tool or
underlying dispatch code path can set an immediate-send/immediate-publish
argument (AC-3) — see app/dispatch.py.

Dual-mode gate: BUFFER_API_KEY (real Key Vault secret name: buffer-api-key
— per environment facts, not docs/credentials-runbook.md's stale
buffer-access-token name). Every call is logged to mcp_ops.tool_calls when
DATABASE_URL is configured (best-effort: logging failures never fail the
tool call itself).
"""

from __future__ import annotations

import os
import time

from fastapi import FastAPI
from mcp_common import MCPServer, ToolDefinition, log_tool_call
from mcp_common.logging import get_connection

from app.dispatch import create_draft, get_post, list_queue

SERVER_NAME = "mcp-buffer"
SERVER_VERSION = "0.1.0"

TOOLS = [
    ToolDefinition(
        name="list_queue",
        description=(
            "List posts currently sitting in Buffer's queue for a channel. Read-only."
        ),
        inputSchema={
            "type": "object",
            "properties": {"channel_id": {"type": "string"}},
            "required": [],
        },
    ),
    ToolDefinition(
        name="get_post",
        description="Read a single Buffer post's current recorded state. Read-only.",
        inputSchema={
            "type": "object",
            "properties": {"post_id": {"type": "string"}},
            "required": ["post_id"],
        },
    ),
    ToolDefinition(
        name="create_draft",
        description=(
            "Create a new queued item in Buffer as a draft-equivalent item. "
            "Always created in a draft state — this tool never transitions a post "
            "to a scheduled or delivered state, and accepts no status/mode/state "
            "argument of any kind."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "channel_id": {"type": "string"},
                "text": {"type": "string"},
                # A1 (attribution). Opaque labels, both optional. Neither
                # is a status, neither is read by Buffer as one, and
                # neither widens what this tool can cause to happen --
                # which is the AC-3 invariant, as distinct from the
                # argument COUNT that was never the safety property.
                # pytest -m mcp_buffer_surface enforces that no
                # status/mode/state property appears here.
                "utm_campaign": {"type": "string"},
                "post_archetype": {"type": "string"},
            },
            "required": ["channel_id", "text"],
        },
    ),
]

_DISPATCH_TABLE = {
    "list_queue": list_queue,
    "get_post": get_post,
    "create_draft": create_draft,
}


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
    fn = _DISPATCH_TABLE.get(tool_name)
    if fn is None:
        _log_best_effort(tool_name, arguments, (time.monotonic() - start) * 1000, "error")
        raise ValueError(f"unknown tool: {tool_name}")
    try:
        result = fn(arguments)
    except Exception:
        _log_best_effort(tool_name, arguments, (time.monotonic() - start) * 1000, "error")
        raise
    else:
        _log_best_effort(tool_name, arguments, (time.monotonic() - start) * 1000, "success")
        return result


mcp_server = MCPServer(name=SERVER_NAME, version=SERVER_VERSION, tools=TOOLS, dispatch=dispatch)

app = FastAPI(title=SERVER_NAME)
mcp_server.mount(app)
