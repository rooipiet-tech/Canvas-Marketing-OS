"""GET /approval-status (plan step 4, AC-15) — agent-native read of the
REAL human decision, no browser/Entra session required."""

from __future__ import annotations

import uuid

from app.approval_inbox import consume_link, create_approval_request


def test_not_found_when_no_matching_row(client) -> None:
    resp = client.get(
        "/approval-status",
        params={"agent_run_id": str(uuid.uuid4()), "function_id": "publish.social_post"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "not_found"


def test_rejects_non_uuid_agent_run_id(client) -> None:
    resp = client.get(
        "/approval-status",
        params={"agent_run_id": "not-a-uuid", "function_id": "publish.social_post"},
    )
    assert resp.status_code == 400


def test_pending_status_reported(conn, agent_run, client) -> None:
    create_approval_request(
        conn,
        gate_decision_id=None,
        agent_run_id=agent_run,
        function_id="publish.social_post",
        action_class="publish",
        level=1,
        content_hash="abc123",
        preview_title="[LOOP-PROOF] publish.social_post (publish)",
        preview_reference="loop-proof://task-1",
        evidence_summary="test",
    )
    resp = client.get(
        "/approval-status",
        params={
            "agent_run_id": str(agent_run),
            "function_id": "publish.social_post",
            "content_hash": "abc123",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "pending"
    assert body["decided_by"] is None


def test_approved_status_reported_after_consume(conn, agent_run, client) -> None:
    approval = create_approval_request(
        conn,
        gate_decision_id=None,
        agent_run_id=agent_run,
        function_id="publish.social_post",
        action_class="publish",
        level=1,
        content_hash="xyz789",
        preview_title="publish.social_post (publish)",
        preview_reference=None,
        evidence_summary="test",
    )
    consume_link(conn, approval["id"], status="approved", decided_by="user@example.com")

    resp = client.get(
        "/approval-status",
        params={
            "agent_run_id": str(agent_run),
            "function_id": "publish.social_post",
            "content_hash": "xyz789",
        },
    )
    body = resp.json()
    assert body["status"] == "approved"
    assert body["decided_by"] == "user@example.com"


def test_distinguishes_from_decision_completion(conn, agent_run, client) -> None:
    """AC-15's core point: request-approval's OWN task state is always
    COMPLETED once /gate-check responds -- this endpoint must report the
    REAL, separate, possibly-still-pending human decision instead."""
    create_approval_request(
        conn,
        gate_decision_id=None,
        agent_run_id=agent_run,
        function_id="publish.social_post",
        action_class="publish",
        level=1,
        content_hash="hash1",
        preview_title="publish.social_post (publish)",
        preview_reference=None,
        evidence_summary="test",
    )
    resp = client.get(
        "/approval-status",
        params={
            "agent_run_id": str(agent_run),
            "function_id": "publish.social_post",
            "content_hash": "hash1",
        },
    )
    assert resp.json()["status"] == "pending"
