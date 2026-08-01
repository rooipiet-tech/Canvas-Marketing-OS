"""Unit tests for orchestrator/clients/* (plan step 3, AC-19, AC-20).

No live network/Azure CLI required: FQDN resolution falls back to None
when `az` is unavailable/unauthenticated (asserted directly), and the
HTTP clients are exercised against httpx.MockTransport doubles rather
than real services.
"""

from __future__ import annotations

import json

import httpx
import pytest
from orchestrator.clients.azure_fqdn import resolve_live_fqdn
from orchestrator.clients.gatekeeper_client import GatekeeperClient, resolve_gatekeeper_base_url
from orchestrator.clients.gateway_client import (
    LOGICAL_TIERS,
    GatewayClientError,
    OrchestratorGatewayClient,
    resolve_gateway_base_url,
)
from orchestrator.clients.mcp_client import MCPClient, MCPClientError, resolve_mcp_web_base_url
from orchestrator.clients.vault_client_ext import VaultClientExt, resolve_vault_base_url


def test_resolve_live_fqdn_never_raises_when_az_unavailable(monkeypatch):
    # In this sandbox `az` may or may not exist; either way this must
    # never raise, and must never fabricate a hostname.
    result = resolve_live_fqdn("ca-does-not-exist", timeout=2.0)
    assert result is None or result.startswith("https://")


def test_env_override_wins_for_every_client(monkeypatch):
    # PERF-04 defense-in-depth added lru_cache(maxsize=1) to each of these
    # 4 resolvers (process-lifetime memoization so a live container's
    # CLI-fallback subprocess only pays its cost once) -- clear each cache
    # first so this test's outcome never depends on whether some other
    # test happened to call the same resolver earlier in this pytest
    # process with a different (or absent) env var set.
    resolve_gateway_base_url.cache_clear()
    resolve_vault_base_url.cache_clear()
    resolve_gatekeeper_base_url.cache_clear()
    resolve_mcp_web_base_url.cache_clear()
    monkeypatch.setenv("CMOS_GATEWAY_BASE_URL", "https://gw.example.invalid")
    monkeypatch.setenv("VAULT_API_URL", "https://vault.example.invalid")
    monkeypatch.setenv("CMOS_GATEKEEPER_BASE_URL", "https://gk.example.invalid")
    monkeypatch.setenv("CMOS_MCP_WEB_BASE_URL", "https://web.example.invalid")
    assert resolve_gateway_base_url() == "https://gw.example.invalid"
    assert resolve_vault_base_url() == "https://vault.example.invalid"
    assert resolve_gatekeeper_base_url() == "https://gk.example.invalid"
    assert resolve_mcp_web_base_url() == "https://web.example.invalid"


def test_gateway_client_rejects_non_logical_model():
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - never reached
        raise AssertionError("must never reach the transport for a bad model")

    client = OrchestratorGatewayClient(
        base_url="http://mock.invalid", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(GatewayClientError):
        client.complete(
            model="claude-sonnet-4-6",  # a physical id — must be rejected (AC-20)
            system_prompt="sys",
            user_content="user",
            agent_run_id="00000000-0000-0000-0000-000000000000",
        )


def test_gateway_client_happy_path():
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["model"] in LOGICAL_TIERS
        return httpx.Response(
            200,
            json={
                "id": "cmpl_1",
                "model": payload["model"],
                "content": "hello",
                "usage": {"input_tokens": 3, "output_tokens": 1},
                "agent_run_id": payload["agent_run_id"],
            },
        )

    client = OrchestratorGatewayClient(
        base_url="http://mock.invalid", transport=httpx.MockTransport(handler)
    )
    result = client.complete(
        model="claude-haiku",
        system_prompt="sys",
        user_content="user",
        agent_run_id="00000000-0000-0000-0000-000000000000",
    )
    assert result["content"] == "hello"


def test_gateway_client_requires_base_url():
    with pytest.raises(GatewayClientError):
        OrchestratorGatewayClient(base_url="")


def test_vault_client_get_or_create_campaign_reuses_existing():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        if request.method == "GET" and request.url.path == "/campaigns":
            return httpx.Response(200, json=[{"id": "existing-id", "name": "run-abc"}])
        raise AssertionError("must not POST a new campaign when one already matches by name")

    with httpx.Client(
        base_url="http://mock.invalid", transport=httpx.MockTransport(handler)
    ) as raw:
        ext = VaultClientExt(base_url="http://mock.invalid")
        ext._client = raw  # swap in the mock transport client
        campaign_id = ext.get_or_create_campaign("run-abc")
    assert campaign_id == "existing-id"
    assert "POST" not in calls


def test_vault_client_get_or_create_campaign_creates_when_absent():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/campaigns":
            return httpx.Response(200, json=[])
        if request.method == "POST" and request.url.path == "/campaigns":
            body = json.loads(request.content)
            assert body["name"] == "run-xyz"
            return httpx.Response(201, json={"id": "new-id", "name": "run-xyz"})
        raise AssertionError(f"unexpected call: {request.method} {request.url.path}")

    with httpx.Client(
        base_url="http://mock.invalid", transport=httpx.MockTransport(handler)
    ) as raw:
        ext = VaultClientExt(base_url="http://mock.invalid")
        ext._client = raw
        campaign_id = ext.get_or_create_campaign("run-xyz")
    assert campaign_id == "new-id"


def test_gatekeeper_client_gate_check():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/gate-check"
        return httpx.Response(
            200,
            json={
                "decision_id": "d1",
                "agent_run_id": "a1",
                "outcome": "escalated",
                "reason": "level_1_requires_approval",
                "level": 1,
                "function_id": "publish.social_post",
                "action_class": "publish",
            },
        )

    client = GatekeeperClient(base_url="http://mock.invalid")
    client._client = httpx.Client(
        base_url="http://mock.invalid", transport=httpx.MockTransport(handler)
    )
    result = client.gate_check(
        agent_run_id="a1", function_id="publish.social_post", action_class="publish"
    )
    assert result["outcome"] == "escalated"


def test_gatekeeper_client_approval_status():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/approval-status"
        return httpx.Response(
            200, json={"status": "pending", "decided_by": None, "decided_at": None}
        )

    client = GatekeeperClient(base_url="http://mock.invalid")
    client._client = httpx.Client(
        base_url="http://mock.invalid", transport=httpx.MockTransport(handler)
    )
    result = client.get_approval_status(agent_run_id="a1", function_id="publish.social_post")
    assert result["status"] == "pending"


def test_mcp_client_call_tool_extracts_structured_content():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["method"] == "tools/call"
        assert body["params"]["name"] == "fetch_url"
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": body["id"],
                "result": {
                    "content": [{"type": "text", "text": "{}"}],
                    "isError": False,
                    "structuredContent": {"source": "fixture", "url": "https://example.com"},
                },
            },
        )

    client = MCPClient(base_url="http://mock.invalid")
    client._client = httpx.Client(
        base_url="http://mock.invalid", transport=httpx.MockTransport(handler)
    )
    result = client.call_tool("fetch_url", {"url": "https://example.com"})
    assert result["source"] == "fixture"


def test_mcp_client_raises_on_jsonrpc_error():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": body["id"],
                "error": {"code": -32000, "message": "boom"},
            },
        )

    client = MCPClient(base_url="http://mock.invalid")
    client._client = httpx.Client(
        base_url="http://mock.invalid", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(MCPClientError):
        client.call_tool("fetch_url", {"url": "https://example.com"})
