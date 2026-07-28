"""AC-10 / AC-11 — per-function daily budgets: soft breach and hard breach."""

from __future__ import annotations

from conftest import AGENT_RUN_ID, completion_payload

AGENT_NAME = "drafting-agent"


def _seed(fake_repo, spend_usd: float) -> None:
    fake_repo.set_agent_name(AGENT_RUN_ID, AGENT_NAME)
    fake_repo.set_daily_spend(AGENT_NAME, spend_usd)


def test_soft_breach_downgrades_tier(app_client, fake_repo, stub_provider):
    # Default policy: 20.00 USD/day, soft breach at 80% => 16.00 USD.
    # 17.00 is past the soft threshold but well below the hard limit, so the
    # request must still succeed — downgraded one tier, never blocked.
    _seed(fake_repo, 17.0)

    response = app_client.post("/v1/completions", json=completion_payload(model="claude-opus"))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["budget_state"] == "soft_breach"
    assert body["routing_tier"] == "sonnet", "opus must be downgraded, not blocked"
    assert stub_provider.call_count == 1


def test_hard_breach_429_and_audit_row(app_client, fake_repo, stub_provider):
    _seed(fake_repo, 25.0)

    response = app_client.post("/v1/completions", json=completion_payload(model="claude-opus"))

    assert response.status_code == 429, response.text
    body = response.json()
    assert "error" in body
    # The upstream provider is never reached on a hard breach.
    assert stub_provider.call_count == 0
    # Exactly one audit row, and the 429 body's queued-task reference is
    # that row's gate_decisions.id.
    rows = fake_repo.gate_decisions.rows
    assert len(rows) == 1
    row = rows[0]
    assert body["queued_task_ref"] == row["id"]
    assert row["decided_by"] == 'system:model-gateway:budget-gate'
    assert row["outcome"] == 'escalated'
    assert row["agent_run_id"] is not None
    assert row["agent_run_id"] == AGENT_RUN_ID
    # No cost rows for a request that never reached the provider.
    assert fake_repo.costs == []
