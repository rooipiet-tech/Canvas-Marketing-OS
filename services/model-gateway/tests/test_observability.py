"""AC-31 — every gateway decision is readable by an agent, not just a human.

Routing tier, cache hit/miss and budget state ride on the response body;
redaction outcome (whose request returns an Error object, not a completion)
rides on the structured JSON log line emitted for every request.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys

import yaml
from conftest import REDACTION_RULES_PATH, SERVICE_ROOT, completion_payload


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

    # ...and on the response body too, so the signal survives any future
    # logging misconfiguration. routing_tier was resolved before the block.
    body = response.json()
    assert body["redaction_outcome"] == "blocked"
    assert body["routing_tier"] == "sonnet"


# --------------------------------------------------------------------------
# The tests above use caplog, which force-enables INFO on the 'model-gateway'
# logger. That is exactly what can mask a service that never configures
# logging at all: in a real container the logger would sit at the root's
# default WARNING with no handlers, and every logger.info(json.dumps(...))
# would be a silent no-op. So the check below deliberately runs a FRESH
# INTERPRETER with no pytest logging plugin in it.
# --------------------------------------------------------------------------

_FRESH_PROCESS_PROBE = (
    "import json, logging, main;"
    "log = logging.getLogger('model-gateway');"
    "print('enabled', log.isEnabledFor(logging.INFO));"
    "print('root_handlers', bool(logging.getLogger().handlers));"
    "log.info(json.dumps({'event': 'probe'}))"
)


def _run_probe() -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", _FRESH_PROCESS_PROBE],
        cwd=str(SERVICE_ROOT),
        capture_output=True,
        text=True,
    )


def test_importing_the_app_configures_logging_in_a_fresh_process():
    result = _run_probe()

    assert result.returncode == 0, result.stderr
    assert "enabled True" in result.stdout
    assert "root_handlers True" in result.stdout


def test_emitted_log_lines_are_bare_json_with_no_level_name_prefix():
    result = _run_probe()
    assert result.returncode == 0, result.stderr

    # basicConfig's StreamHandler writes to stderr.
    lines = [line for line in result.stderr.splitlines() if line.strip()]
    assert lines, "the info log line never reached a handler in a fresh process"
    # A log-scraping agent must be able to json.loads the line as-is: no
    # 'INFO:model-gateway:' prefix in front of the JSON document.
    assert json.loads(lines[-1]) == {"event": "probe"}
