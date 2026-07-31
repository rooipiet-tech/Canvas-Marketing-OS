"""mcp_common — shared library for Canvas Marketing OS's MCP tool plane.

Used by mcp-web, mcp-buffer, and mcp-canva. Provides:
  - credentials: dual-mode secret resolution (env var -> Key Vault SDK ->
    absent/fixture-mode), plus the mcp-web non-secret feature-flag helper.
  - logging: mcp_ops.tool_calls logging wrapper (caller identity, arguments
    hash, latency, outcome).
  - protocol: a minimal, dependency-free (no external MCP SDK) MCP-over-
    HTTP scaffold: initialize / tools-list / tools-call over a single
    POST /mcp JSON-RPC 2.0 endpoint.
"""

from mcp_common.credentials import flag_enabled, is_live_mode, resolve_secret
from mcp_common.logging import OUTCOMES, hash_arguments, log_tool_call, logged_tool_call
from mcp_common.protocol import JSONRPCRequest, JSONRPCResponse, MCPServer, ToolDefinition

__all__ = [
    "resolve_secret",
    "is_live_mode",
    "flag_enabled",
    "OUTCOMES",
    "hash_arguments",
    "log_tool_call",
    "logged_tool_call",
    "MCPServer",
    "ToolDefinition",
    "JSONRPCRequest",
    "JSONRPCResponse",
]
