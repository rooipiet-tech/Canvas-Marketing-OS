"""AC-12 / AC-13 — kill switches at the gate-check decision point.

The switch is a direct, uncached read on every decision, so a flip is
visible to the very next request — comfortably inside the 5s bound.
"""

from __future__ import annotations

import time


def _gate_check(client, agent_run, function_id="analyse.signal", action_class="analyse"):
    return client.post(
        "/gate-check",
        json={
            "agent_run_id": str(agent_run),
            "function_id": function_id,
            "action_class": action_class,
            "content_hash": "d" * 64,
        },
    ).json()


def test_global_kill_switch_blocks_gate_check_within_5s(client, conn, agent_run) -> None:
    allowed = _gate_check(client, agent_run)
    assert allowed["outcome"] == "approved"

    flipped_at = time.monotonic()
    conn.execute(
        "INSERT INTO governance.kill_switches (scope, reason) VALUES ('global', 'ops halt')"
    )

    blocked = _gate_check(client, agent_run)
    elapsed = time.monotonic() - flipped_at

    assert blocked["outcome"] == "rejected"
    assert "kill_switch_active:global" in blocked["reason"]
    assert elapsed < 5.0, f"kill switch took {elapsed:.2f}s to take effect"


def test_per_function_kill_switch_scopes_gate_check(client, conn, agent_run) -> None:
    conn.execute(
        """
        INSERT INTO governance.kill_switches (scope, function_id, reason)
        VALUES ('function', 'analyse.signal', 'quarantined')
        """
    )

    blocked = _gate_check(client, agent_run, "analyse.signal", "analyse")
    assert blocked["outcome"] == "rejected"
    assert "kill_switch_active:function:analyse.signal" in blocked["reason"]

    # A different function_id is entirely unaffected.
    unaffected = _gate_check(client, agent_run, "analyse.campaign_performance", "analyse")
    assert unaffected["outcome"] == "approved"
    assert "kill_switch" not in (unaffected["reason"] or "")
