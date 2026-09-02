"""A1 — a published post carries the labels measurement needs.

THE GAP THIS CLOSES. `analytics.post_archetype` is read by
orchestrator/db.py:413's engagement query and rendered by
dispatch.py:4110's month-end report, and until now nothing anywhere
wrote it. The headline KPI -- engagement rate by post archetype --
grouped on a column no writer filled, so
rollup_engagement_by_archetype's `WHERE post_archetype IS NOT NULL`
matched nothing and the KPI was structurally empty. `scheduled_posts`
and `utm_campaign_map` were the first two join keys to be closed; this
is the third and last.

Both labels come off the asset JSON the publisher ALREADY fetches
(app/vault_lookup.py's GET /assets/{id}) -- no extra request and no new
failure mode.

THE CONSTRAINT THAT SHAPES THIS. AC-09 is a real safety invariant:
create_draft must never accept an argument that can transition a post's
state. These two are opaque labels; neither is a status. The invariant
is asserted directly in test_buffer_draft_only_invariant.py, which was
rewritten alongside this work to check the PROPERTY (no
status/mode/state parameter) rather than the argument COUNT it used to
pin.

WHY ATTRIBUTION MUST NEVER BLOCK A PUBLISH. A missing content_hash or
agent_run_id fails closed, because publishing the wrong bytes is
unrecoverable. A missing archetype costs a NULL in a KPI group. Those
are not the same risk, and the tests below pin that they are not treated
as the same risk.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from app.buffer_client import BufferClient
from app.vault_lookup import fetch_asset_and_agent_name

ASSET_ID = "asset-1"
RUN_ID = "run-1"


def _vault_client(asset: dict[str, Any]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == f"/assets/{ASSET_ID}":
            return httpx.Response(200, json=asset)
        if request.url.path == f"/agent-runs/{RUN_ID}":
            return httpx.Response(200, json={"id": RUN_ID, "agent_name": "linkedin-post-writer"})
        raise AssertionError(f"unexpected path {request.url.path}")

    return httpx.Client(base_url="http://vault.invalid", transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------------
# Resolving the labels from the lookup that already runs
# ---------------------------------------------------------------------


def test_asset_type_and_campaign_come_from_the_existing_asset_fetch():
    asset = {
        "id": ASSET_ID,
        "content_hash": "abc123",
        "agent_run_id": RUN_ID,
        "asset_type": "carousel",
        "campaign": "fabric-production-proof",
    }

    result = fetch_asset_and_agent_name(ASSET_ID, http_client=_vault_client(asset))

    assert result.asset_type == "carousel"
    assert result.campaign == "fabric-production-proof"
    # And the fields the fail-closed contract depends on are untouched.
    assert result.content_hash == "abc123"
    assert result.agent_run_id == RUN_ID


def test_an_asset_without_the_labels_still_resolves():
    """A missing archetype is a reporting gap, never a refusal to publish.

    The Vault's assets table has carried asset_type since the frozen
    schema, but a row written before that, or by a path that omits it,
    must not turn into a publish failure.
    """
    asset = {"id": ASSET_ID, "content_hash": "abc123", "agent_run_id": RUN_ID}

    result = fetch_asset_and_agent_name(ASSET_ID, http_client=_vault_client(asset))

    assert result.asset_type is None
    assert result.campaign is None
    assert result.content_hash == "abc123"


# ---------------------------------------------------------------------
# Forwarding them without widening the publish surface
# ---------------------------------------------------------------------


def _capture(handler_result: dict[str, Any] | None = None):
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        captured.update(body["params"]["arguments"])
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": body["id"],
                "result": {"structuredContent": handler_result or {"source": "fixture"}},
            },
        )

    return captured, handler


def _client(handler) -> BufferClient:
    client = BufferClient(base_url="http://mock-buffer.invalid")
    client._client = httpx.Client(  # noqa: SLF001 - mirrors test_buffer_client.py's _with_mock
        base_url="http://mock-buffer.invalid", transport=httpx.MockTransport(handler)
    )
    return client


def test_both_labels_reach_create_draft_when_present():
    captured, handler = _capture()

    _client(handler).create_draft(
        channel_id="chan-1",
        text="hello world",
        utm_campaign="fabric-production-proof",
        post_archetype="carousel",
    )

    assert captured["utm_campaign"] == "fabric-production-proof"
    assert captured["post_archetype"] == "carousel"
    # Still nothing that could transition state.
    assert not ({"status", "mode", "state"} & set(captured))


def test_the_archetype_is_the_vault_asset_type_verbatim():
    """The archetype vocabulary IS the Vault asset_type vocabulary.

    This is a decision, not an accident, and it is pinned here because
    the alternative is silent: a translation layer added later would
    populate the KPI with values nothing else in the system uses, and
    nothing would fail.

    Why verbatim is the right call. `analytics.post_archetype` has never
    had a writer -- that is the whole of A1 -- so there is no historical
    data whose vocabulary must be preserved. The first writer defines it,
    and the only vocabulary the rest of the system actually speaks is the
    one dispatch.py stamps onto assets: carousel_post, case_study,
    content_derivatives, linkedin_post, newsletter.

    NOTE, adjacent and deliberately not changed here: the analytics test
    fixtures (tests/fixtures/buffer_2026-07-31.json) use `carousel` and
    `story`, neither of which is a real asset_type. They are illustrative
    values chosen before any writer existed, and they are what the
    existing rollup tests assert against. They are now unrealistic rather
    than wrong -- worth aligning, but that touches those tests' golden
    expectations and belongs in its own change.
    """
    asset = {
        "id": ASSET_ID,
        "content_hash": "abc123",
        "agent_run_id": RUN_ID,
        "asset_type": "carousel_post",
        "campaign": "fabric-production-proof",
    }
    captured, handler = _capture()

    result = fetch_asset_and_agent_name(ASSET_ID, http_client=_vault_client(asset))
    _client(handler).create_draft(
        channel_id="chan-1",
        text="hello",
        utm_campaign=result.campaign,
        post_archetype=result.asset_type,
    )

    assert captured["post_archetype"] == "carousel_post", (
        "the archetype must reach Buffer as the raw Vault asset_type -- a silent "
        "translation would populate the KPI with a vocabulary nothing else uses"
    )


def test_absent_labels_are_omitted_rather_than_sent_as_null():
    """Keeps the wire shape identical to before A1 when there is nothing
    to attribute -- which is what lets
    test_create_draft_sends_only_channel_id_and_text_never_a_status_argument
    keep asserting an exact two-key payload."""
    captured, handler = _capture()

    _client(handler).create_draft(channel_id="chan-1", text="hello world")

    assert set(captured) == {"channel_id", "text"}
