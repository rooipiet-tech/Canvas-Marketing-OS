"""mcp-buffer client for Publisher's live-mode create_draft path (plan
step 14; AC-08, AC-09).

A separate, self-contained implementation of the MCP-over-HTTP JSON-RPC
protocol itself (Publisher shares no application library with
orchestrator, same reasoning as verifier.py's own "standalone by design"
note) talking mcp_common.protocol.MCPServer's protocol (initialize /
tools/list / tools/call) against mcp-buffer's real 3 tools: list_queue,
get_post, create_draft.

FQDN resolution is the one piece of this NOT reimplemented here (TD-16):
`resolve_live_fqdn` used to be a hand-duplicated copy, byte-near-identical
to services/orchestrator/orchestrator/clients/azure_fqdn.py's and
services/registry/gateway_client.py's own, with no test coverage of its
own. It now comes from the shared azure_client_lib package -- zero
third-party dependencies, so it can be embedded as plain source into this
BUNDLE-deployed service the same way governance-lib already is (see
infra/main.bicep's comment above gatekeeperUnpackScript for the pattern,
and azure_client_lib's own pyproject.toml for why that matters).

create_draft here NEVER threads a status/mode/state argument through to
mcp-buffer (AC-09) -- the only arguments sent are channel_id and text,
matching mcp-buffer's own tools.yaml inputSchema exactly, which itself
hardcodes `_CREATE_DRAFT_STATUS = "draft"` server-side and accepts no
caller-supplied override.
"""

from __future__ import annotations

import itertools
import os
from typing import Any

import httpx
from azure_client_lib import resolve_live_fqdn

AZURE_CONTAINER_APP = "mcp-buffer"

_id_counter = itertools.count(1)


class BufferClientError(RuntimeError):
    """mcp-buffer could not be reached, or returned a JSON-RPC error."""


def resolve_mcp_buffer_base_url() -> str | None:
    """CMOS_MCP_BUFFER_BASE_URL env override wins (tests / non-Azure
    runs); otherwise resolve mcp-buffer's real live FQDN (AC-19-equivalent
    discipline for Publisher, L-0025) -- never a hardcoded hostname."""
    override = os.environ.get("CMOS_MCP_BUFFER_BASE_URL")
    if override:
        return override
    return resolve_live_fqdn(AZURE_CONTAINER_APP)


class BufferClient:
    def __init__(self, *, base_url: str, timeout: float = 20.0) -> None:
        if not base_url:
            raise BufferClientError(
                "BufferClient requires a resolved base_url — never a guessed hostname "
                "(L-0025); call resolve_mcp_buffer_base_url() first"
            )
        self._client = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> BufferClient:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    def _call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        request_id = next(_id_counter)
        body = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        }
        response = self._client.post("/mcp", json=body)
        if response.status_code != 200:
            raise BufferClientError(
                f"POST /mcp returned HTTP {response.status_code}: {response.text[:500]}"
            )
        envelope = response.json()
        error = envelope.get("error")
        if error is not None:
            raise BufferClientError(f"tools/call {tool_name!r} returned a JSON-RPC error: {error}")
        result = envelope.get("result") or {}
        structured = result.get("structuredContent")
        if structured is None:
            raise BufferClientError(f"tools/call {tool_name!r} carried no structuredContent")
        return structured

    def list_queue_count(self, channel_id: str) -> int:
        """Live count of posts currently queued for `channel_id` (DE-3's
        enforcement point — a dynamic check, never a static assumption
        alone, since other actors/tools could also add to the same
        queue between runs).

        mcp-buffer/app/dispatch.py's list_queue returns one of two
        shapes depending on mode: fixture mode flattens the fixture's own
        `posts` key to the top level (`{"source": "fixture", "posts":
        [...]}`); live mode wraps the raw GraphQL response
        (`{"source": "live", "result": {"data": {"posts": [...]}}}`).
        Handles both; defaults to an empty queue (count 0) rather than
        guessing on an unrecognized shape — under-counting here is safe
        (it can only make the cap check MORE conservative on the next
        call, never let an over-cap request through silently, since a
        live create_draft would still be gated by mcp-buffer's own state).
        """
        result = self._call_tool("list_queue", {"channel_id": channel_id})
        posts = result.get("posts")
        if posts is None and isinstance(result.get("result"), dict):
            posts = result["result"].get("data", {}).get("posts")
        return len(posts) if isinstance(posts, list) else 0

    def create_draft(
        self,
        *,
        channel_id: str,
        text: str,
        utm_campaign: str | None = None,
        post_archetype: str | None = None,
    ) -> dict[str, Any]:
        """No status/mode/state argument, ever (AC-09).

        This docstring used to read "the ONLY 2 arguments ever sent",
        which stated the count rather than the invariant. The count was
        never the safety property: AC-09 is that nothing in this call can
        transition a post's state. `utm_campaign` and `post_archetype`
        are opaque attribution labels -- neither is a status, neither is
        read by Buffer as one, and neither widens what this method can
        cause to happen. The guard that matters is enforced in
        mcp-buffer's own surface tests (pytest -m mcp_buffer_surface):
        create_draft's inputSchema must expose no status/mode/state
        property, and its dispatch module must contain no
        published/now/immediate/sendNow/publishNow literal.

        Both labels are optional and default to None. A post whose asset
        carries no archetype still publishes -- see AssetLookupResult's
        note on why a reporting label must never be able to refuse a
        publish.
        """
        arguments: dict[str, Any] = {"channel_id": channel_id, "text": text}
        if utm_campaign:
            arguments["utm_campaign"] = utm_campaign
        if post_archetype:
            arguments["post_archetype"] = post_archetype
        return self._call_tool("create_draft", arguments)
