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


def test_a_metering_write_failure_never_fails_the_completion(
    monkeypatch, app_client, fake_repo, stub_provider, gateway_log
):
    """Availability first: a completion that already succeeded upstream must
    still return 200 with real content even if the costs write itself blows
    up — the expensive work is already done and paid for by that point.
    """

    async def _boom(**kwargs):
        raise RuntimeError("connection to postgres lost")

    monkeypatch.setattr(fake_repo, "insert_costs_rows", _boom)

    response = app_client.post("/v1/completions", json=completion_payload())

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["content"] == stub_provider.content
    assert "cost_id" not in body  # omitted, not null — contract-safe either way
    assert fake_repo.costs == []  # the write genuinely never landed

    # ...but it must be impossible to miss server-side.
    failures = [
        line for line in gateway_log.json_lines() if line.get("event") == "metering_write_failed"
    ]
    assert len(failures) == 1
    assert failures[0]["agent_run_id"] == AGENT_RUN_ID
    assert failures[0]["error_type"] == "RuntimeError"
