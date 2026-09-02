"""AC-08(b)/(c) — live mode enforces the 10-post free-tier cap via a live
list_queue count check against BUFFER_FREE_TIER_QUEUE_CAP, and calls
create_draft against the corrected LinkedIn channel id (68e73facca3a4e6b746d17b4)
when under the cap.
"""

from __future__ import annotations

import base64

import app.routers.publish as publish_router
from app.config import BUFFER_FREE_TIER_QUEUE_CAP, BUFFER_LINKEDIN_CHANNEL_ID

ASSET_BYTES = b"a client-free LinkedIn proof point post for the cap test."


class _FakeBufferClient:
    def __init__(self, queue_count: int, calls: list) -> None:
        self._queue_count = queue_count
        self._calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        pass

    def list_queue_count(self, channel_id):
        self._calls.append(("list_queue_count", channel_id))
        return self._queue_count

    def create_draft(
        self, *, channel_id, text, utm_campaign=None, post_archetype=None
    ):
        self._calls.append(("create_draft", channel_id, text))
        return {"post": {"id": "fake-post-1", "status": "draft"}}


def _publish(client, agent_run, token):
    return client.post(
        "/publish",
        json={
            "agent_run_id": str(agent_run),
            "function_id": "publish.social_post",
            "asset_bytes_b64": base64.b64encode(ASSET_BYTES).decode(),
            "gate_token": token,
        },
    )


def test_live_mode_at_cap_refuses_without_ever_calling_create_draft(
    client, conn, agent_run, gate_decision, make_token, monkeypatch
) -> None:
    from app.hashing import recompute_content_hash

    monkeypatch.setenv("PUBLISHER_DRY_RUN", "false")
    calls: list = []
    monkeypatch.setattr(
        publish_router,
        "BufferClient",
        lambda base_url: _FakeBufferClient(BUFFER_FREE_TIER_QUEUE_CAP, calls),
    )

    content_hash = recompute_content_hash(ASSET_BYTES)
    token, _claims = make_token(gate_decision_id=gate_decision, content_hash=content_hash)

    response = _publish(client, agent_run, token)
    assert response.status_code == 403
    assert response.json()["detail"]["reason"] == "buffer_queue_cap_exceeded"
    assert ("create_draft", BUFFER_LINKEDIN_CHANNEL_ID, ASSET_BYTES.decode()) not in calls
    assert not any(call[0] == "create_draft" for call in calls)


def test_live_mode_under_cap_calls_create_draft_against_linkedin_channel(
    client, conn, agent_run, gate_decision, make_token, monkeypatch
) -> None:
    from app.hashing import recompute_content_hash

    monkeypatch.setenv("PUBLISHER_DRY_RUN", "false")
    calls: list = []
    monkeypatch.setattr(
        publish_router,
        "BufferClient",
        lambda base_url: _FakeBufferClient(BUFFER_FREE_TIER_QUEUE_CAP - 1, calls),
    )

    content_hash = recompute_content_hash(ASSET_BYTES)
    token, _claims = make_token(gate_decision_id=gate_decision, content_hash=content_hash)

    response = _publish(client, agent_run, token)
    assert response.status_code == 200
    assert response.json()["reason"] == "published"
    assert ("create_draft", BUFFER_LINKEDIN_CHANNEL_ID, ASSET_BYTES.decode()) in calls
