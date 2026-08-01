"""Unit tests for app/vault_lookup.py (plan step 14, PV3-01) — no live
Postgres or Vault needed, exercised against httpx.MockTransport doubles
via the injectable `http_client` parameter."""

from __future__ import annotations

import httpx
import pytest
from app.vault_lookup import VaultLookupError, fetch_asset_and_agent_name


def _mock_client(handler) -> httpx.Client:
    return httpx.Client(base_url="http://vault.invalid", transport=httpx.MockTransport(handler))


def test_fetch_asset_and_agent_name_happy_path():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/assets/asset-1":
            return httpx.Response(
                200, json={"id": "asset-1", "content_hash": "abc123", "agent_run_id": "run-1"}
            )
        if request.url.path == "/agent-runs/run-1":
            return httpx.Response(200, json={"id": "run-1", "agent_name": "loop-proof-circuit"})
        raise AssertionError(f"unexpected path {request.url.path}")

    result = fetch_asset_and_agent_name("asset-1", http_client=_mock_client(handler))
    assert result.content_hash == "abc123"
    assert result.agent_name == "loop-proof-circuit"
    assert result.agent_run_id == "run-1"


def test_fetch_asset_and_agent_name_fails_closed_on_404():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "not found"})

    with pytest.raises(VaultLookupError):
        fetch_asset_and_agent_name("missing-asset", http_client=_mock_client(handler))


def test_fetch_asset_and_agent_name_fails_closed_on_missing_fields():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "asset-1"})  # no content_hash/agent_run_id

    with pytest.raises(VaultLookupError):
        fetch_asset_and_agent_name("asset-1", http_client=_mock_client(handler))


def test_fetch_asset_and_agent_name_fails_closed_on_agent_run_lookup_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/assets/asset-1":
            return httpx.Response(
                200, json={"id": "asset-1", "content_hash": "abc123", "agent_run_id": "run-1"}
            )
        return httpx.Response(500, json={"error": "boom"})

    with pytest.raises(VaultLookupError):
        fetch_asset_and_agent_name("asset-1", http_client=_mock_client(handler))


def test_fetch_asset_and_agent_name_requires_base_url(monkeypatch):
    monkeypatch.delenv("VAULT_API_URL", raising=False)
    with pytest.raises(VaultLookupError):
        fetch_asset_and_agent_name("asset-1", base_url=None)
