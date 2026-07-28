"""AC-02 — a level-1 gate-check with no prior approval is blocked and audited."""

from __future__ import annotations


def test_level1_without_approval_is_blocked_and_audited(client, conn, agent_run) -> None:
    response = client.post(
        "/gate-check",
        json={
            "agent_run_id": str(agent_run),
            "function_id": "publish.social_post",
            "action_class": "publish",
            "content_hash": "a" * 64,
        },
    )
    assert response.status_code == 200
    body = response.json()

    # Denied: no gate token is issued, and the outcome is not `approved`.
    assert body["level"] == 1
    assert body["outcome"] != "approved"
    assert body["outcome"] == "escalated"
    assert body["gate_token"] is None
    assert "level_1_requires_approval" in body["reason"]

    # Exactly one gate_decisions row, with a non-approved outcome and a
    # reason mentioning level_1_requires_approval.
    rows = conn.execute(
        "SELECT id, agent_run_id, outcome, reason FROM gate_decisions WHERE agent_run_id = %s",
        (agent_run,),
    ).fetchall()
    assert len(rows) == 1
    assert str(rows[0]["id"]) == body["decision_id"]
    assert rows[0]["outcome"] != "approved"
    assert "level_1_requires_approval" in rows[0]["reason"]
