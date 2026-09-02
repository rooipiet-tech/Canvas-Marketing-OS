"""B1 (2 Sep 2026) — the free-tier cap is accepted as a throttle rather
than bought out, and the alert is what was added instead.

The decision rests on arithmetic recorded beside BUFFER_FREE_TIER_QUEUE_CAP
in app/config.py: the loop runs one complete content cycle per DAY, each
cycle can queue up to four posts to one channel, and the cap is ten. So
the cap binds within days of a stalled queue -- at which point the fault
is the channel's posting schedule, not the tier. The publisher emits a
structured warning while the queue can still be drained --
BUFFER_QUEUE_DEPTH_WARN_AT (6), one full cycle of headroom below the cap,
because an alert that first fires at the cap fires only once posts are
already being refused.

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

    def create_draft(self, *, channel_id, text, **_labels):
        # **_labels deliberately. A1 (PR #129) adds optional attribution
        # kwargs (utm_campaign, post_archetype) to the real
        # BufferClient.create_draft, and a double that accepts fewer
        # keywords than the thing it stands in for raises TypeError on a
        # call the real client handles fine. That is exactly the break A1
        # had to fix in four other files. Absorbing them here means this
        # branch and that one can merge in either order.
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
    # One cycle's worth of posts sitting in the queue is a completely
    # healthy state -- they are waiting for their scheduled slots -- and
    # must produce no signal at all.
    response, _calls, warnings = _run_at_depth(
        4, client, agent_run, gate_decision, make_token, monkeypatch, caplog
    )

    assert response.status_code == 200
    assert warnings == []


def test_the_warning_threshold_leaves_one_whole_cycle_of_headroom() -> None:
    """The decision's own arithmetic, asserted rather than left in a
    comment -- and the guard against the error this change was corrected
    for.

    The loop fires one COMPLETE content cycle per day (the authoritative
    source is infra/modules/scheduling/weekly-planning-trigger.bicep's
    `frequency: 'Day'`, not weekly-content-loop.yaml's name), and a cycle
    can queue up to four posts. A threshold with less than one cycle of
    headroom gives under a day's notice, which is not warning, it is
    narration.
    """
    posts_per_cycle = 4
    assert BUFFER_QUEUE_DEPTH_WARN_AT < BUFFER_FREE_TIER_QUEUE_CAP
    headroom = BUFFER_FREE_TIER_QUEUE_CAP - BUFFER_QUEUE_DEPTH_WARN_AT
    assert headroom >= posts_per_cycle, (
        "the warning must land at least one full cycle before the cap, or "
        "the queue can go from quiet to rejecting between two alerts"
    )
