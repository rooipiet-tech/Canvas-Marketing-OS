"""mcp-buffer dispatch — read-queue + create-draft ONLY (AC-2, AC-3).

Buffer's real GraphQL mutation shape is UNVERIFIED (no live access) — per
domain constraint C-2, treated defensively: no exposed tool, and no code
path reachable from any exposed tool's argument surface, may set an
immediate-publish/immediate-send argument or mode on any Buffer API call.

`create_draft`'s status is hardcoded server-side (`_CREATE_DRAFT_STATUS`
below) to a draft-equivalent value and is NEVER accepted as a
caller-supplied argument — no tool's inputSchema exposes a free-form
status/mode/state parameter at all (see tools.yaml).

`_graphql_request` is the ONLY function in this module that ever makes a
real Buffer API call; every caller passes it a hardcoded, module-local
query/mutation string — never a caller-supplied query.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from mcp_common import is_live_mode

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"

BUFFER_API_URL = os.environ.get("BUFFER_API_URL", "https://api.buffer.com/graphql")
BUFFER_ORG_ID = os.environ.get("BUFFER_ORG_ID", "68e5f2187fe9a5263a3509ab")


def _live_mode() -> bool:
    return is_live_mode("BUFFER_API_KEY", "buffer-api-key")


def _load_fixture(name: str) -> dict:
    path = FIXTURES_DIR / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _graphql_request(query: str, variables: dict, *, http_client=None) -> dict:
    import httpx

    api_key = os.environ.get("BUFFER_API_KEY")
    client = http_client if http_client is not None else httpx
    response = client.post(
        BUFFER_API_URL,
        json={"query": query, "variables": variables},
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=10.0,
    )
    return response.json()


# ---------------------------------------------------------------------
# list_queue — read-only: lists items currently queued, never a
# state-transitioning call.
# ---------------------------------------------------------------------

_LIST_QUEUE_QUERY = """
query ListQueue($organizationId: ID!, $channelId: ID) {
  posts(organizationId: $organizationId, channelId: $channelId, status: QUEUE) {
    id
    text
    channelId
    dueAt
    status
  }
}
"""


def list_queue(arguments: dict, *, http_client=None) -> dict:
    channel_id = arguments.get("channel_id")
    if not _live_mode():
        fixture = _load_fixture("list_queue")
        return {"source": "fixture", **fixture}
    data = _graphql_request(
        _LIST_QUEUE_QUERY,
        {"organizationId": BUFFER_ORG_ID, "channelId": channel_id},
        http_client=http_client,
    )
    return {"source": "live", "result": data}


# ---------------------------------------------------------------------
# get_post — read-only: reads a single post's current recorded state.
# ---------------------------------------------------------------------

_GET_POST_QUERY = """
query GetPost($postId: ID!) {
  post(id: $postId) {
    id
    text
    channelId
    dueAt
    status
  }
}
"""


def get_post(arguments: dict, *, http_client=None) -> dict:
    post_id = arguments.get("post_id")
    if not _live_mode():
        fixture = _load_fixture("get_post")
        return {"source": "fixture", **fixture}
    data = _graphql_request(_GET_POST_QUERY, {"postId": post_id}, http_client=http_client)
    return {"source": "live", "result": data}


# ---------------------------------------------------------------------
# create_draft — create/idea-shaped only. Status is hardcoded below and
# never accepted from `arguments` (no status/mode/state key is ever read
# from caller input in this function).
# ---------------------------------------------------------------------

_CREATE_DRAFT_STATUS = "draft"  # hardcoded, server-side only — never caller-controllable

_CREATE_DRAFT_MUTATION = """
mutation CreateDraft($organizationId: ID!, $channelId: ID!, $text: String!, $status: PostStatus!) {
  createPost(organizationId: $organizationId, channelId: $channelId, text: $text, status: $status) {
    id
    status
  }
}
"""


def create_draft(arguments: dict, *, http_client=None) -> dict:
    channel_id = arguments.get("channel_id")
    text = arguments.get("text", "")

    # A1 (attribution). Echoed back to the caller and recorded by
    # main.py's mcp_ops.tool_calls logging, which is what actually
    # captures publish-time attribution on our side of the boundary.
    #
    # They are NOT added to the mutation below, and that is a finding
    # rather than an omission. Introspecting api.buffer.com's live schema
    # (2 Sep 2026) shows Buffer has no field that is both writable on
    # create and readable back on the post:
    #   * CreatePostInput.tagIds is [TagId!] -- references to EXISTING
    #     Tag entities. There is no createTag mutation in the schema and
    #     OrganizationLimits.tags caps them, so a free-form archetype
    #     label cannot be a tag.
    #   * CreatePostInput.source is a free-form String, but the Post type
    #     exposes no `source` field, so it can be written and never read.
    #   * Post exposes no archetype/utmCampaign field of any kind.
    # The one carrier that does round-trip is the post text itself, which
    # already contains the CTA URL and its utm_* parameters -- so the
    # archetype travels as utm_content on that URL, per the remediation
    # backlog's own stated fallback, and analytics reads it back from
    # Post.text rather than from a Buffer metadata field that does not
    # exist.
    utm_campaign = arguments.get("utm_campaign")
    post_archetype = arguments.get("post_archetype")
    attribution = {
        key: value
        for key, value in (("utm_campaign", utm_campaign), ("post_archetype", post_archetype))
        if value
    }

    if not _live_mode():
        fixture = _load_fixture("create_draft")
        return {"source": "fixture", **fixture, **attribution}
    data = _graphql_request(
        _CREATE_DRAFT_MUTATION,
        {
            "organizationId": BUFFER_ORG_ID,
            "channelId": channel_id,
            "text": text,
            "status": _CREATE_DRAFT_STATUS,
        },
        http_client=http_client,
    )
    return {"source": "live", "result": data, **attribution}
