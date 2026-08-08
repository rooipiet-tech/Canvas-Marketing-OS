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
from conftest import (
    AGENT_RUN_ID,
    REDACTION_RULES_PATH,
    SERVICE_ROOT,
    StubProvider,
    completion_payload,
)
from providers import registry


def test_response_body_carries_routing_cache_and_budget_state(app_client, stub_provider):
    response = app_client.post("/v1/completions", json=completion_payload(task_ref="obs-1"))
    assert response.status_code == 200
    body = response.json()

    assert body["routing_tier"] == "sonnet"
    assert body["cache_hit"] is False
    assert body["budget_state"] == "ok"

    repeat = app_client.post("/v1/completions", json=completion_payload(task_ref="obs-1"))
    assert repeat.json()["cache_hit"] is True


def test_structured_log_line_covers_every_decision(app_client, stub_provider, gateway_log):
    app_client.post("/v1/completions", json=completion_payload())

    logged = gateway_log.json_lines()
    assert logged, "expected at least one structured JSON log line"
    entry = logged[-1]
    assert entry["routing_tier"] == "sonnet"
    assert entry["cache_hit"] is False
    assert entry["budget_state"] == "ok"
    assert entry["redaction_outcome"] == "ok"


def test_blocked_request_reports_its_redaction_outcome_in_the_log(
    app_client, fake_repo, stub_provider, gateway_log
):
    rules = yaml.safe_load(REDACTION_RULES_PATH.read_text(encoding="utf-8"))
    client_name = rules["fixtures"]["client_names"][0]
    payload = completion_payload()
    payload["messages"] = [
        {"role": "user", "content": f"summarise the brief for {client_name}"}
    ]

    response = app_client.post("/v1/completions", json=payload)
    assert response.status_code == 400

    logged = gateway_log.json_lines()
    assert logged[-1]["redaction_outcome"] == "blocked"
    assert "redaction" in gateway_log.text

    # ...and on the response body too, so the signal survives any future
    # logging misconfiguration. routing_tier was resolved before the block.
    body = response.json()
    assert body["redaction_outcome"] == "blocked"
    assert body["routing_tier"] == "sonnet"


# --------------------------------------------------------------------------
# F-EMPTY-COMPLETION-VISIBILITY, 7 Aug 2026, round 24. A real production
# incident: qa-review-brand-steward dead-lettered 3x on "model response was
# not valid JSON: Expecting value: line 1 column 1 (char 0)" -- a 200 from
# the provider whose content had zero text blocks -- with nothing in THIS
# service's own logs to say why. stop_reason is now threaded through from
# the provider adapter (see providers/base.py's ProviderResult and
# providers/anthropic.py) precisely so the next occurrence is diagnosable
# from model-gateway's logs alone, not reconstructed after the fact from an
# orchestrator-side parse error three retries later.
# --------------------------------------------------------------------------


def test_stop_reason_rides_on_the_response_body_even_when_content_is_present(
    app_client, monkeypatch
):
    provider = StubProvider(content="a normal completion", stop_reason="end_turn")
    monkeypatch.setattr(registry, "get_provider", lambda name: provider)

    response = app_client.post("/v1/completions", json=completion_payload())

    assert response.status_code == 200
    assert response.json()["stop_reason"] == "end_turn"


def test_empty_completion_content_logs_a_warning_with_stop_reason(
    app_client, monkeypatch, gateway_log
):
    provider = StubProvider(content="", stop_reason="max_tokens")
    monkeypatch.setattr(registry, "get_provider", lambda name: provider)

    response = app_client.post("/v1/completions", json=completion_payload())

    # Still a normal 200 -- the provider genuinely answered, empty content is
    # a real (if unusual) response, not a gateway-level failure to surface as
    # an error to the caller. The point of this test is that it's no longer
    # a silent dead end.
    assert response.status_code == 200
    assert response.json()["content"] == ""
    assert response.json()["stop_reason"] == "max_tokens"

    warnings = [r for r in gateway_log.records if r.levelno == logging.WARNING]
    assert warnings, "expected a WARNING-level log line for the empty completion"
    entry = json.loads(warnings[-1].getMessage())
    assert entry["event"] == "empty_completion_content"
    assert entry["stop_reason"] == "max_tokens"
    assert entry["agent_run_id"] == AGENT_RUN_ID


def test_whitespace_only_completion_content_also_triggers_the_warning(
    app_client, monkeypatch, gateway_log
):
    """Not just literally "" -- a provider that returns only whitespace is
    exactly as dead-ended for a JSON-parsing caller, so it must trip the same
    signal rather than silently passing as "had content"."""
    provider = StubProvider(content="   \n", stop_reason="max_tokens")
    monkeypatch.setattr(registry, "get_provider", lambda name: provider)

    app_client.post("/v1/completions", json=completion_payload())

    warnings = [r for r in gateway_log.records if r.levelno == logging.WARNING]
    assert warnings, "whitespace-only content must trigger the same empty-completion warning"


def test_non_empty_completion_content_never_logs_the_empty_completion_warning(
    app_client, stub_provider, gateway_log
):
    app_client.post("/v1/completions", json=completion_payload())

    warnings = [r for r in gateway_log.records if r.levelno == logging.WARNING]
    assert not warnings, "a normal, non-empty completion must never trip the empty-content warning"


# --------------------------------------------------------------------------
# The tests above attach a handler to the real 'model-gateway' logger without
# forcing its level, so a service that never configured logging would produce
# empty captures rather than silently passing. The checks below go one step
# further and run a FRESH INTERPRETER with no pytest plugins in it at all,
# which is the only way to observe what the container actually does.
# --------------------------------------------------------------------------

_FRESH_PROCESS_PROBE = (
    "import json, logging, main;"
    "log = logging.getLogger('model-gateway');"
    "print('enabled', log.isEnabledFor(logging.INFO));"
    "print('gateway_handlers', bool(log.handlers));"
    "print('propagates', log.propagate);"
    "print('root_handlers', bool(logging.getLogger().handlers));"
    "print('root_level', logging.getLogger().level);"
    "log.info(json.dumps({'event': 'probe'}))"
)

# Same process, but a third-party library logs at INFO first. httpx emits
# exactly this line on every real provider call.
_THIRD_PARTY_PROBE = (
    "import json, logging, main;"
    "logging.getLogger('httpx').info("
    "'HTTP Request: POST https://provider.invalid/v1/messages \"HTTP/1.1 200 OK\"');"
    "logging.getLogger('httpcore').info('connect_tcp.started');"
    "logging.getLogger('model-gateway').info(json.dumps({'event': 'probe'}))"
)


def _run(source: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", source],
        cwd=str(SERVICE_ROOT),
        capture_output=True,
        text=True,
    )


def _run_probe() -> subprocess.CompletedProcess:
    return _run(_FRESH_PROCESS_PROBE)


def test_importing_the_app_configures_logging_in_a_fresh_process():
    result = _run_probe()

    assert result.returncode == 0, result.stderr
    assert "enabled True" in result.stdout
    assert "gateway_handlers True" in result.stdout


def test_logging_config_is_scoped_to_the_gateway_logger_only():
    """Importing the app must not reconfigure logging for the whole process.

    A root-level basicConfig would attach a handler to root AND drop its
    level to INFO, switching on INFO for every third-party library in the
    process. The root logger must be left exactly as Python leaves it: no
    handlers, level WARNING (30) — which is what keeps httpx quiet.
    """
    result = _run_probe()

    assert result.returncode == 0, result.stderr
    assert "root_handlers False" in result.stdout
    assert f"root_level {logging.WARNING}" in result.stdout
    # ...and the gateway's own records stop at its handler rather than
    # flowing up through a root logger somebody else may configure later.
    assert "propagates False" in result.stdout


def test_third_party_info_logs_never_reach_the_json_stream():
    """AC-31: every line on the stream must be a parseable JSON document.

    httpx logs one plain-text line per request at INFO. It must stay
    invisible — its logger inherits the untouched root level (WARNING), so
    the record is dropped before any handler sees it.
    """
    result = _run(_THIRD_PARTY_PROBE)
    assert result.returncode == 0, result.stderr

    stream = result.stderr + result.stdout
    assert "HTTP Request" not in stream
    assert "connect_tcp" not in stream

    lines = [line for line in stream.splitlines() if line.strip()]
    assert lines, "the gateway's own info line never reached a handler"
    # Every single line on the stream parses as JSON — that is the property.
    assert [json.loads(line) for line in lines] == [{"event": "probe"}]


def test_emitted_log_lines_are_bare_json_with_no_level_name_prefix():
    result = _run_probe()
    assert result.returncode == 0, result.stderr

    # The gateway logger's StreamHandler writes to stderr.
    lines = [line for line in result.stderr.splitlines() if line.strip()]
    assert lines, "the info log line never reached a handler in a fresh process"
    # A log-scraping agent must be able to json.loads the line as-is: no
    # 'INFO:model-gateway:' prefix in front of the JSON document.
    assert json.loads(lines[-1]) == {"event": "probe"}
