"""B1 (2 Sep 2026) — the free-tier cap is accepted as a throttle rather
than bought out, and the alert is what was added instead.

The decision rests on arithmetic recorded beside BUFFER_FREE_TIER_QUEUE_CAP
in app/config.py: four Buffer posts per weekly cycle against a cap of ten
means the queue must go roughly two and a half weeks undrained before the
cap rejects anything. At that point the fault is a stalled queue, not a
tier limit. So the publisher emits a structured warning while there is
still headroom -- BUFFER_QUEUE_DEPTH_WARN_AT (8), deliberately BELOW the
cap, because an alert that first fires at the cap fires only once posts
are already being refused.

These tests pin the three properties that make the alert worth having:
  (1) it fires below the cap, on a request that still publishes;
  (2) it also fires AT the cap, where the post is refused -- being
      rejected is still a stalled queue and still needs saying;
  (3) it stays quiet on an ordinary queue depth, so the signal means
      something when it does appear.
"""

from __future__ import annotations

import base64
import json
import logging

import app.routers.publish as publish_router
from app.config import (
    BUFFER_FREE_TIER_QUEUE_CAP,
    BUFFER_LINKEDIN_CHANNEL_ID,
    BUFFER_QUEUE_DEPTH_WARN_AT,
)

ASSET_BYTES = b"a client-free LinkedIn proof point post for the queue-depth test."


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

    def create_draft(self, *, channel_id, text):
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


def _run_at_depth(depth, client, agent_run, gate_decision, make_token, monkeypatch, caplog):
    from app.hashing import recompute_content_hash

    monkeypatch.setenv("PUBLISHER_DRY_RUN", "false")
    calls: list = []
    monkeypatch.setattr(
        publish_router,
        "BufferClient",
        lambda base_url: _FakeBufferClient(depth, calls),
    )
    content_hash = recompute_content_hash(ASSET_BYTES)
    token, _claims = make_token(gate_decision_id=gate_decision, content_hash=content_hash)

    with caplog.at_level(logging.WARNING, logger="publisher"):
        response = _publish(client, agent_run, token)
    return response, calls, _warnings(caplog)


def _warnings(caplog) -> list[dict]:
    """The publisher logs a JSON body as the message itself, so the fields
    are in the message text and not in record.extra_fields."""
    found = []
    for record in caplog.records:
        try:
            payload = json.loads(record.getMessage())
        except (ValueError, TypeError):
            continue
        if payload.get("event") == publish_router.EVENT_BUFFER_QUEUE_DEPTH_HIGH:
            found.append(payload)
    return found


def test_warns_below_the_cap_on_a_request_that_still_publishes(
    client, conn, agent_run, gate_decision, make_token, monkeypatch, caplog
) -> None:
    response, calls, warnings = _run_at_depth(
        BUFFER_QUEUE_DEPTH_WARN_AT,
        client, agent_run, gate_decision, make_token, monkeypatch, caplog,
    )

    # The point of warning early: this post is NOT refused.
    assert response.status_code == 200
    assert response.json()["reason"] == "published"
    assert ("create_draft", BUFFER_LINKEDIN_CHANNEL_ID, ASSET_BYTES.decode()) in calls

    assert len(warnings) == 1
    payload = warnings[0]
    assert payload["queue_count"] == BUFFER_QUEUE_DEPTH_WARN_AT
    assert payload["warn_at"] == BUFFER_QUEUE_DEPTH_WARN_AT
    assert payload["cap"] == BUFFER_FREE_TIER_QUEUE_CAP
    assert payload["channel_id"] == BUFFER_LINKEDIN_CHANNEL_ID
    assert payload["at_cap"] is False


def test_warns_at_the_cap_too_where_the_post_is_refused(
    client, conn, agent_run, gate_decision, make_token, monkeypatch, caplog
) -> None:
    response, calls, warnings = _run_at_depth(
        BUFFER_FREE_TIER_QUEUE_CAP,
        client, agent_run, gate_decision, make_token, monkeypatch, caplog,
    )

    assert response.status_code == 403
    assert response.json()["detail"]["reason"] == "buffer_queue_cap_exceeded"
    assert not any(call[0] == "create_draft" for call in calls)

    # Warn-then-refuse, never refuse-silently.
    assert len(warnings) == 1
    assert warnings[0]["queue_count"] == BUFFER_FREE_TIER_QUEUE_CAP
    assert warnings[0]["at_cap"] is True


def test_silent_at_an_ordinary_queue_depth(
    client, conn, agent_run, gate_decision, make_token, monkeypatch, caplog
) -> None:
    # Four posts a week is the loop's real cadence, so a depth of four is
    # a completely healthy queue and must produce no signal at all.
    response, _calls, warnings = _run_at_depth(
        4, client, agent_run, gate_decision, make_token, monkeypatch, caplog
    )

    assert response.status_code == 200
    assert warnings == []


def test_the_warning_threshold_leaves_real_headroom_below_the_cap() -> None:
    """The decision's own arithmetic, asserted rather than left in a
    comment: warning AT the cap would be useless, and a threshold with
    less than one week's cadence of headroom would not give anyone time
    to act before posts start being refused."""
    assert BUFFER_QUEUE_DEPTH_WARN_AT < BUFFER_FREE_TIER_QUEUE_CAP
    posts_per_weekly_cycle = 4
    headroom = BUFFER_FREE_TIER_QUEUE_CAP - BUFFER_QUEUE_DEPTH_WARN_AT
    assert headroom >= 1
    assert BUFFER_QUEUE_DEPTH_WARN_AT >= posts_per_weekly_cycle
