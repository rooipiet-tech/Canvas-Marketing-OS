"""Unit tests for app/vault_adapter.py's real Vault write (TD-02) — no
live Postgres or Vault needed, exercised against httpx.MockTransport
doubles via the injectable `http_client` parameter, exactly as
tests/test_vault_lookup.py does for app/vault_lookup.py."""

from __future__ import annotations

import httpx
import pytest
from app.vault_adapter import VaultWriteError, write_gate_decision

SOURCE_ID = "gate-decision-1"
TAXONOMY = {
    "vertical": "mobility",
    "campaign": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
    "evidence_grade": "A",
    "consent_status": "not_required",
    "retention_class": "standard_1y",
}


def _mock_client(handler) -> httpx.Client:
    return httpx.Client(base_url="http://vault.invalid", transport=httpx.MockTransport(handler))


def test_write_gate_decision_happy_path():
    posted = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == f"/gate-decisions/{SOURCE_ID}":
            return httpx.Response(200, json={"id": SOURCE_ID, **TAXONOMY})
        if request.method == "POST" and request.url.path == "/gate-decisions":
            import json

            posted.update(json.loads(request.content))
            return httpx.Response(201, json={**posted, "id": "new-decision-1"})
        raise AssertionError(f"unexpected call {request.method} {request.url.path}")

    record_id = write_gate_decision(
        agent_run_id="run-1",
        function_id="publish.social_post",
        reason="published (content_hash=abc, jti=xyz)",
        source_gate_decision_id=SOURCE_ID,
        http_client=_mock_client(handler),
    )

    assert record_id == "new-decision-1"
    assert posted["agent_run_id"] == "run-1"
    assert posted["decided_by"] == "service:publisher"
    assert posted["outcome"] == "approved"
    assert posted["function_id"] == "publish.social_post"
    assert posted["vertical"] == "mobility"
    assert posted["campaign"] == TAXONOMY["campaign"]
    assert posted["evidence_grade"] == "A"
    assert posted["consent_status"] == "not_required"
    assert posted["retention_class"] == "standard_1y"


def test_write_gate_decision_fails_closed_on_source_lookup_404():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "not found"})

    with pytest.raises(VaultWriteError):
        write_gate_decision(
            agent_run_id="run-1",
            function_id="publish.social_post",
            reason="published",
            source_gate_decision_id=SOURCE_ID,
            http_client=_mock_client(handler),
        )


def test_write_gate_decision_fails_closed_on_missing_taxonomy_field():
    def handler(request: httpx.Request) -> httpx.Response:
        incomplete = dict(TAXONOMY)
        incomplete.pop("evidence_grade")
        return httpx.Response(200, json={"id": SOURCE_ID, **incomplete})

    with pytest.raises(VaultWriteError):
        write_gate_decision(
            agent_run_id="run-1",
            function_id="publish.social_post",
            reason="published",
            source_gate_decision_id=SOURCE_ID,
            http_client=_mock_client(handler),
        )


def test_write_gate_decision_fails_closed_on_create_error():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"id": SOURCE_ID, **TAXONOMY})
        return httpx.Response(500, json={"error": "boom"})

    with pytest.raises(VaultWriteError):
        write_gate_decision(
            agent_run_id="run-1",
            function_id="publish.social_post",
            reason="published",
            source_gate_decision_id=SOURCE_ID,
            http_client=_mock_client(handler),
        )


def test_write_gate_decision_fails_closed_on_create_response_missing_id():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"id": SOURCE_ID, **TAXONOMY})
        return httpx.Response(201, json={})

    with pytest.raises(VaultWriteError):
        write_gate_decision(
            agent_run_id="run-1",
            function_id="publish.social_post",
            reason="published",
            source_gate_decision_id=SOURCE_ID,
            http_client=_mock_client(handler),
        )


def test_write_gate_decision_requires_base_url(monkeypatch):
    monkeypatch.delenv("VAULT_API_URL", raising=False)
    with pytest.raises(VaultWriteError):
        write_gate_decision(
            agent_run_id="run-1",
            function_id="publish.social_post",
            reason="published",
            source_gate_decision_id=SOURCE_ID,
            base_url=None,
        )
