"""AC-31 — every gateway decision is readable by an agent, not just a human.

Routing tier, cache hit/miss and budget state ride on the response body;
redaction outcome (whose request returns an Error object, not a completion)
rides on the structured JSON log line emitted for every request.
"""

from __future__ import annotations

import json
import logging

import yaml
from conftest import REDACTION_RULES_PATH, completion_payload


def _log_records(caplog) -> list[dict]:
    records = []
    for record in caplog.records:
        try:
            records.append(json.loads(record.getMessage()))
        except (TypeError, ValueError):
            continue
    return records


def test_response_body_carries_routing_cache_and_budget_state(app_client, stub_provider):
    response = app_client.post("/v1/completions", json=completion_payload(task_ref="obs-1"))
    assert response.status_code == 200
    body = response.json()

    assert body["routing_tier"] == "sonnet"
    assert body["cache_hit"] is False
    assert body["budget_state"] == "ok"

    repeat = app_client.post("/v1/completions", json=completion_payload(task_ref="obs-1"))
    assert repeat.json()["cache_hit"] is True


def test_structured_log_line_covers_every_decision(app_client, stub_provider, caplog):
    caplog.set_level(logging.INFO, logger="model-gateway")

    app_client.post("/v1/completions", json=completion_payload())

    logged = _log_records(caplog)
    assert logged, "expected at least one structured JSON log line"
    entry = logged[-1]
    assert entry["routing_tier"] == "sonnet"
    assert entry["cache_hit"] is False
    assert entry["budget_state"] == "ok"
    assert entry["redaction_outcome"] == "ok"


def test_blocked_request_reports_its_redaction_outcome_in_the_log(
    app_client, fake_repo, stub_provider, caplog
):
    caplog.set_level(logging.INFO, logger="model-gateway")
    rules = yaml.safe_load(REDACTION_RULES_PATH.read_text(encoding="utf-8"))
    client_name = rules["fixtures"]["client_names"][0]
    payload = completion_payload()
    payload["messages"] = [
        {"role": "user", "content": f"summarise the brief for {client_name}"}
    ]

    response = app_client.post("/v1/completions", json=payload)
    assert response.status_code == 400

    logged = _log_records(caplog)
    assert logged[-1]["redaction_outcome"] == "blocked"
    assert "redaction" in caplog.text
