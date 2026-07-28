"""AC-12 — every successful completion writes exactly three costs rows."""

from __future__ import annotations

from conftest import AGENT_RUN_ID, completion_payload


def test_one_completion_writes_three_costs_rows(app_client, fake_repo, stub_provider):
    response = app_client.post("/v1/completions", json=completion_payload())
    assert response.status_code == 200, response.text

    rows = fake_repo.costs
    assert len(rows) == 3

    usd_rows = [r for r in rows if r['unit'] == 'usd']
    token_rows = [r for r in rows if r['unit'] == 'tokens']
    ms_rows = [r for r in rows if r['unit'] == 'ms']

    assert len(usd_rows) == 1
    assert len(token_rows) == 1
    assert len(ms_rows) == 1

    # tokens row carries input + output.
    assert token_rows[0]["amount"] == 12 + 8
    assert usd_rows[0]["amount"] > 0
    assert ms_rows[0]["amount"] >= 0

    for row in rows:
        assert row["agent_run_id"] == AGENT_RUN_ID
        assert row["provider"]

    # cost_id on the response points at the usd row.
    assert response.json()["cost_id"] == usd_rows[0]["id"]
