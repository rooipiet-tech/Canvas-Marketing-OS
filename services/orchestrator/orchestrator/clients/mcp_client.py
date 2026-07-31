"""Minimal MCP-over-HTTP JSON-RPC client (AC-01, AC-19, AC-24).

Talks the same POST /mcp JSON-RPC 2.0 protocol mcp_common.protocol.MCPServer
implements (initialize / tools/list / tools/call) — no external MCP SDK
dependency, mirroring mcp_common's own "from-scratch scaffold" choice.
This session's ingest-signals handler only ever calls mcp-web's fetch_url
tool (AC-24) — the client itself is generic over server/tool so it isn't
special-cased to one tool name.
"""

from __future__ import annotations

import itertools
import os
from typing import Any

import httpx

from orchestrator.clients.azure_fqdn import resolve_live_fqdn
from orchestrator.telemetry_wiring import inject_traceparent

AZURE_CONTAINER_APP_MCP_WEB = "mcp-web"

_id_counter = itertools.count(1)


class MCPClientError(RuntimeError):
    """The MCP server could not be reached, or returned a JSON-RPC error."""


def resolve_mcp_web_base_url() -> str | None:
    """CMOS_MCP_WEB_BASE_URL env override wins; otherwise resolve mcp-web's
    real live FQDN (AC-19, L-0025)."""
    override = os.environ.get("CMOS_MCP_WEB_BASE_URL")
    if override:
        return override
    return resolve_live_fqdn(AZURE_CONTAINER_APP_MCP_WEB)


class MCPClient:
    def __init__(self, *, base_url: str, timeout: float = 20.0) -> None:
        if not base_url:
            raise MCPClientError(
                "MCPClient requires a resolved base_url — never a guessed hostname "
                "(L-0025); call resolve_mcp_web_base_url() first"
            )
        self._client = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> MCPClient:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        request_id = next(_id_counter)
        body = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        }
        response = self._client.post("/mcp", json=body, headers=inject_traceparent())
        if response.status_code != 200:
            raise MCPClientError(
                f"POST /mcp returned HTTP {response.status_code}: {response.text[:500]}"
            )
        envelope = response.json()
        error = envelope.get("error")
        if error is not None:
            raise MCPClientError(f"tools/call {tool_name!r} returned a JSON-RPC error: {error}")
        result = envelope.get("result") or {}
        structured = result.get("structuredContent")
        if structured is None:
            raise MCPClientError(
                f"tools/call {tool_name!r} response carried no structuredContent"
            )
        return structured
