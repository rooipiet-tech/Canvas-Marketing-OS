"""mcp-canva — MCP server: template-locked design creation, bulk CSV, export.

Exactly 3 tools (AC-16): create_design_from_template, bulk_create_from_csv
(both template-locked — no blank/free-form design creation path exists),
export_design. Dual-mode gate: CANVA_CLIENT_ID + CANVA_CLIENT_SECRET (real
Key Vault secret names — see app/dispatch.py). Every call is logged to
mcp_ops.tool_calls when DATABASE_URL is configured (best-effort: logging
failures never fail the tool call itself).
"""

from __future__ import annotations

import os
import time

from fastapi import FastAPI
from mcp_common import MCPServer, ToolDefinition, log_tool_call
from mcp_common.logging import get_connection

from app.dispatch import bulk_create_from_csv, create_design_from_template, export_design

SERVER_NAME = "mcp-canva"
SERVER_VERSION = "0.1.0"

TOOLS = [
    ToolDefinition(
        name="create_design_from_template",
        description=(
            "Create a new Canva design from a brand template. template_id is "
            "always required — no blank/free-form design creation is possible "
            "through this tool."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "template_id": {"type": "string"},
                "title": {"type": "string"},
            },
            "required": ["template_id"],
        },
    ),
    ToolDefinition(
        name="bulk_create_from_csv",
        description=(
            "Bulk-create designs from a brand template by autofilling one row "
            "of CSV-shaped data per design. template_id is always required, "
            "same as create_design_from_template."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "template_id": {"type": "string"},
                "rows": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "One object per CSV row of autofill data.",
                },
            },
            "required": ["template_id", "rows"],
        },
    ),
    ToolDefinition(
        name="export_design",
        description="Export an already-created Canva design to a file format (e.g. PNG).",
        inputSchema={
            "type": "object",
            "properties": {"design_id": {"type": "string"}},
            "required": ["design_id"],
        },
    ),
]

_DISPATCH_TABLE = {
    "create_design_from_template": create_design_from_template,
    "bulk_create_from_csv": bulk_create_from_csv,
    "export_design": export_design,
}


def _caller_identity() -> str:
    return os.environ.get("MCP_CALLER_IDENTITY", "unknown")


def _log_best_effort(tool_name: str, arguments: dict, latency_ms: float, outcome: str) -> None:
    try:
        conn = get_connection()
    except Exception:
        return  # DATABASE_URL not configured — logging is best-effort, never fatal.
    try:
        log_tool_call(
            conn,
            server_name=SERVER_NAME,
            tool_name=tool_name,
            caller_identity=_caller_identity(),
            arguments=arguments,
            latency_ms=latency_ms,
            outcome=outcome,
        )
    finally:
        conn.close()


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
