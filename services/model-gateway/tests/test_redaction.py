"""AC-15 — a fixture client name is blocked before any provider call.

The seeded value comes from contracts/model-gateway/redaction-rules.yaml's
fixtures section, read here rather than hand-duplicated, so the contract and
this test cannot drift. The firewall's scan scope is every
messages[*].content string plus the serialized tools[] payload.
"""

from __future__ import annotations

import completion
import redaction
import yaml
from conftest import AGENT_RUN_ID, REDACTION_RULES_PATH, completion_payload


def _fixture_client_name() -> str:
    rules = yaml.safe_load(REDACTION_RULES_PATH.read_text(encoding="utf-8"))
    return rules["fixtures"]["client_names"][0]


def _content_block_payload(client_name: str) -> dict:
    """A non-string `content`: the shape the real Anthropic API also accepts."""
    payload = completion_payload()
    payload["messages"] = [
        {"role": "user", "content": [{"type": "text", "text": f"brief for {client_name}"}]}
    ]
    return payload


def test_client_name_in_message_is_blocked_and_audited(app_client, fake_repo, stub_provider):
    client_name = _fixture_client_name()
    payload = completion_payload()
    payload["messages"] = [
        {"role": "user", "content": f"draft a thank-you note for {client_name}"}
    ]

    response = app_client.post("/v1/completions", json=payload)

    assert 400 <= response.status_code < 500
    body = response.json()
    assert "error" in body
    assert body["error"]["code"] == "REDACTION_BLOCKED"

    # The provider adapter is never reached.
    assert stub_provider.call_count == 0

    rows = fake_repo.gate_decisions.rows
    assert len(rows) == 1
    row = rows[0]
    assert row["decided_by"] == 'system:model-gateway:redaction-firewall'
    assert row["outcome"] == 'rejected'
    assert row["agent_run_id"] is not None
    assert row["agent_run_id"] == AGENT_RUN_ID
    assert fake_repo.costs == []


def test_client_name_smuggled_into_tools_is_also_blocked(app_client, fake_repo, stub_provider):
    client_name = _fixture_client_name()
    payload = completion_payload()
    payload["tools"] = [
        {
            "name": "lookup_account",
            "description": f"look up the account for {client_name}",
            "input_schema": {"type": "object"},
        }
    ]

    response = app_client.post("/v1/completions", json=payload)

    assert response.status_code == 400
    assert stub_provider.call_count == 0
    assert len(fake_repo.gate_decisions.rows) == 1


def test_non_string_content_never_reaches_the_provider(app_client, fake_repo, stub_provider):
    """Layer 1: a content-block array violates the frozen contract's
    `content: type: string` and is rejected at the boundary, so nothing
    unexpected is forwarded upstream unscanned."""
    payload = _content_block_payload(_fixture_client_name())

    response = app_client.post("/v1/completions", json=payload)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"
    assert stub_provider.call_count == 0


def test_client_name_in_a_content_block_array_is_scanned_not_skipped(monkeypatch):
    """Layer 2 (unit): the firewall itself must not skip a shape it did not
    expect. Same block outcome as the plain-string case."""
    client_name = _fixture_client_name()

    blocked = redaction.scan_request(_content_block_payload(client_name))
    plain = completion_payload()
    plain["messages"] = [{"role": "user", "content": f"brief for {client_name}"}]

    assert blocked.blocked is True
    assert blocked.matched_pattern_id == redaction.scan_request(plain).matched_pattern_id
    assert redaction.scan_request(completion_payload()).blocked is False


def test_content_block_array_still_blocks_and_audits_if_validation_is_loosened(
    app_client, fake_repo, stub_provider, monkeypatch
):
    """Belt-and-suspenders proof: with layer 1 disabled, a fixture client name
    hidden in a content-block array produces exactly the same block + audit
    outcome as the plain-string case — 400 REDACTION_BLOCKED, zero provider
    calls, one gate_decisions row."""
    monkeypatch.setattr(completion, "validate_request", lambda payload: None)

    response = app_client.post(
        "/v1/completions", json=_content_block_payload(_fixture_client_name())
    )

    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "REDACTION_BLOCKED"
    assert stub_provider.call_count == 0

    rows = fake_repo.gate_decisions.rows
    assert len(rows) == 1
    assert rows[0]["decided_by"] == 'system:model-gateway:redaction-firewall'
    assert rows[0]["outcome"] == 'rejected'
    assert rows[0]["agent_run_id"] == AGENT_RUN_ID
    assert fake_repo.costs == []


def test_clean_payload_is_not_blocked(app_client, fake_repo, stub_provider):
    response = app_client.post("/v1/completions", json=completion_payload())
    assert response.status_code == 200
    assert stub_provider.call_count == 1
    assert fake_repo.gate_decisions.rows == []
