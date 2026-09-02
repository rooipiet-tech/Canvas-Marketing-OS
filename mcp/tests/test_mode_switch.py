"""AC-4: fixture vs. live mode is selected purely by presence/absence of
each server's secret (or, for mcp-web, non-secret flag) env var — no code
or config file edit is needed to switch modes.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.mcp_mode_switch


class _DummyResponse:
    status_code = 200
    text = "live-dummy-body"

    @staticmethod
    def json():
        return {"data": {"posts": []}, "result": "ok"}


def test_mcp_web_mode_switch(monkeypatch, server_app):
    module = server_app("mcp-web")
    result = module.dispatch("fetch_url", {"url": "https://example.com/synthetic"})
    assert result["source"] == "fixture"

    monkeypatch.setenv("MCP_WEB_LIVE_MODE", "true")
    import httpx

    monkeypatch.setattr(httpx, "get", lambda *a, **k: _DummyResponse())

    module = server_app("mcp-web")
    result = module.dispatch("fetch_url", {"url": "https://example.com/synthetic"})
    assert result["source"] == "live"


def test_mcp_buffer_mode_switch(monkeypatch, server_app):
    module = server_app("mcp-buffer")
    result = module.dispatch("list_queue", {})
    assert result["source"] == "fixture"

    monkeypatch.setenv("BUFFER_API_KEY", "dummy-buffer-key")
    import httpx

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _DummyResponse())

    module = server_app("mcp-buffer")
    result = module.dispatch("list_queue", {})
    assert result["source"] == "live"


def test_mcp_canva_mode_switch(monkeypatch, server_app):
    """A3 (2 Sep 2026) MOVED THIS TEST, deliberately.

    It used to assert that CANVA_CLIENT_ID + CANVA_CLIENT_SECRET alone
    flipped mcp-canva to live. That was the contract the code implemented
    and it was the wrong one: both secrets are wired into the deployed
    Container App from Key Vault while canva-refresh-token has never been
    populated, so the deployed server believed it was live and would have
    sent `Authorization: Bearer None` on every call. It also contradicted
    docs/credentials-runbook.md, which has always said mcp-canva stays in
    fixture mode until a token exists.

    So the middle case below is new and is the one that matters:
    credentials WITHOUT a token must stay in fixture mode. Live now needs
    a token as well, which is what the last case supplies.
    """
    module = server_app("mcp-canva")
    result = module.dispatch("export_design", {"design_id": "SYNTH-DESIGN-0001"})
    assert result["source"] == "fixture"

    monkeypatch.setenv("CANVA_CLIENT_ID", "dummy-client-id")
    monkeypatch.setenv("CANVA_CLIENT_SECRET", "dummy-client-secret")
    import httpx

    monkeypatch.setattr(httpx, "request", lambda *a, **k: _DummyResponse())

    # Credentials present, no token obtainable: still fixture. _DummyResponse
    # carries no access_token, so the refresh exchange yields nothing --
    # exactly the deployed state today.
    module = server_app("mcp-canva")
    result = module.dispatch("export_design", {"design_id": "SYNTH-DESIGN-0001"})
    assert result["source"] == "fixture", (
        "client id + secret without a usable token must NOT read as live"
    )

    monkeypatch.setenv("CANVA_ACCESS_TOKEN", "dummy-access-token")
    module = server_app("mcp-canva")
    result = module.dispatch("export_design", {"design_id": "SYNTH-DESIGN-0001"})
    assert result["source"] == "live"
