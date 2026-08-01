"""mcp_common.protocol — minimal MCP-over-HTTP protocol scaffold (AC-1).

Implements just enough of the Model Context Protocol surface for this
build's conformance requirements: `initialize`, `tools/list`, `tools/call`,
via a single POST /mcp JSON-RPC 2.0 endpoint, plus a GET /health probe.
Deliberately does NOT depend on an external MCP SDK package (none exists
in this repo's dependency set — researcher finding) — this is a
from-scratch, FastAPI-native scaffold mirroring services/model-gateway's
existing FastAPI shape.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Iterable

from fastapi import APIRouter, FastAPI, Request
from pydantic import BaseModel, Field

from mcp_common.telemetry import close_tool_call_span, configure_tracer, open_tool_call_span

DispatchFn = Callable[[str, dict[str, Any]], dict[str, Any]]


class ToolDefinition(BaseModel):
    name: str
    description: str
    inputSchema: dict[str, Any] = Field(default_factory=dict)


class JSONRPCRequest(BaseModel):
    jsonrpc: str = "2.0"
    id: int | str | None = None
    method: str
    params: dict[str, Any] = Field(default_factory=dict)


class JSONRPCResponse(BaseModel):
    jsonrpc: str = "2.0"
    id: int | str | None = None
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


def _to_text(result: dict[str, Any]) -> str:
    return json.dumps(result, default=str)


class MCPServer:
    """Wires a server name/version/tool list/dispatch callable to a
    FastAPI router implementing initialize / tools-list / tools-call."""

    def __init__(
        self,
        *,
        name: str,
        version: str,
        tools: Iterable[ToolDefinition],
        dispatch: DispatchFn,
    ) -> None:
        self.name = name
        self.version = version
        self.tools = list(tools)
        self._dispatch = dispatch

    def _handle_initialize(self) -> dict[str, Any]:
        return {
            "protocolVersion": "2024-11-05",
            "serverInfo": {"name": self.name, "version": self.version},
            "capabilities": {"tools": {}},
        }

    def _handle_tools_list(self) -> dict[str, Any]:
        return {"tools": [t.model_dump() for t in self.tools]}

    def _handle_tools_call(self, params: dict[str, Any]) -> dict[str, Any]:
        tool_name = params.get("name")
        arguments = params.get("arguments") or {}
        known = {t.name for t in self.tools}
        if tool_name not in known:
            raise ValueError(f"unknown tool: {tool_name}")
        result = self._dispatch(tool_name, arguments)
        return {
            "content": [{"type": "text", "text": _to_text(result)}],
            "isError": False,
            "structuredContent": result,
        }

    def handle(self, request: JSONRPCRequest) -> JSONRPCResponse:
        try:
            if request.method == "initialize":
                result = self._handle_initialize()
            elif request.method == "tools/list":
                result = self._handle_tools_list()
            elif request.method == "tools/call":
                result = self._handle_tools_call(request.params)
            else:
                return JSONRPCResponse(
                    id=request.id,
                    error={"code": -32601, "message": f"method not found: {request.method}"},
                )
            return JSONRPCResponse(id=request.id, result=result)
        except Exception as exc:  # defensive: never 500 on a bad tool call
            return JSONRPCResponse(id=request.id, error={"code": -32000, "message": str(exc)})

    def build_router(self) -> APIRouter:
        router = APIRouter()

        @router.post("/mcp")
        def _mcp_endpoint(request: JSONRPCRequest, http_request: Request) -> JSONRPCResponse:
            # AC-03/AC-04/DE-5: joins the caller's trace (orchestrator/
            # clients/mcp_client.py and Publisher's app/buffer_client.py
            # both already inject traceparent on every outbound call).
            # Span setup/teardown is isolated from self.handle()'s own
            # exception handling (handle() already never raises — see its
            # own try/except — so there is nothing for telemetry to mask
            # here, but the same open/close-around-a-finally shape is
            # used for consistency with every other adopting service).
            tool_name = (
                request.params.get("name")
                if request.method == "tools/call"
                else request.method
            )
            span_cm, token, _span = open_tool_call_span(
                self.name, http_request.headers, tool_name=str(tool_name)
            )
            try:
                return self.handle(request)
            finally:
                close_tool_call_span(span_cm, token)

        @router.get("/health")
        def _health() -> dict[str, str]:
            return {"status": "ok", "server": self.name}

        return router

    def mount(self, app: FastAPI) -> None:
        app.include_router(self.build_router())
        # Best-effort (AC-03/AC-04) -- a missing
        # APPLICATIONINSIGHTS_CONNECTION_STRING (this sandbox, most local
        # dev, before an App Insights resource exists) is completely
        # normal and must never crash startup. Called directly here
        # (mount() already runs once, at module-import time, the same
        # point every mcp-* server's main.py currently calls it) rather
        # than via a deprecated `@app.on_event("startup")` hook.
        try:
            configure_tracer(self.name)
        except Exception:  # noqa: BLE001 - telemetry must never crash startup
            pass
