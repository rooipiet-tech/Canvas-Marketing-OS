"""AC-03 — every decision path appends exactly one gate_decisions row.

The companion half of AC-03 is a static check run outside pytest: a
repo-wide grep across both services for in-place mutation of the
gate_decisions table must return no matches. gate_decisions is
append-only by convention (the frozen schema has no trigger enforcing
it), so the discipline lives in app/routers/decisions.py, whose only
write statement against that table is an INSERT.
"""

from __future__ import annotations

import pytest

PATHS = [
    # (label, function_id, action_class, kill_switch, expected_outcome, reason_fragment)
    ("approve", "analyse.signal", "analyse", None, "approved", "level_4_autonomous_passthrough"),
    (
        "reject-missing-approval",
        "publish.social_post",
        "publish",
        None,
        "escalated",
        "level_1_requires_approval",
    ),
    (
        "reject-kill-switch",
        "analyse.signal",
        "analyse",
        "global",
        "rejected",
        "kill_switch_active",
    ),
    ("reject-level-0", "publish.paid_ad", "publish", None, "rejected", "level_0_blocked"),
]


@pytest.mark.parametrize(
    "label,function_id,action_class,kill_switch,expected_outcome,reason_fragment",
    PATHS,
    ids=[row[0] for row in PATHS],
)
def test_every_decision_path_inserts_one_row(
    client,
    conn,
    agent_run,
    label,
    function_id,
    action_class,
    kill_switch,
    expected_outcome,
    reason_fragment,
) -> None:
    if kill_switch == "global":
        conn.execute(
            "INSERT INTO governance.kill_switches (scope, reason) VALUES ('global', %s)",
            (f"{label} test",),
        )

    before = conn.execute("SELECT count(*) AS n FROM gate_decisions").fetchone()["n"]

    response = client.post(
        "/gate-check",
        json={
            "agent_run_id": str(agent_run),
            "function_id": function_id,
            "action_class": action_class,
            "content_hash": "c" * 64,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == expected_outcome
    assert reason_fragment in body["reason"]

    after = conn.execute("SELECT count(*) AS n FROM gate_decisions").fetchone()["n"]
    assert after - before == 1, f"{label}: expected exactly one new gate_decisions row"

    row = conn.execute(
        "SELECT outcome, reason FROM gate_decisions WHERE id = %s", (body["decision_id"],)
    ).fetchone()
    assert row["outcome"] == expected_outcome
    assert reason_fragment in row["reason"]
