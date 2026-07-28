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
    module = server_app("mcp-canva")
    result = module.dispatch("export_design", {"design_id": "SYNTH-DESIGN-0001"})
    assert result["source"] == "fixture"

    monkeypatch.setenv("CANVA_CLIENT_ID", "dummy-client-id")
    monkeypatch.setenv("CANVA_CLIENT_SECRET", "dummy-client-secret")
    import httpx

    monkeypatch.setattr(httpx, "request", lambda *a, **k: _DummyResponse())

    module = server_app("mcp-canva")
    result = module.dispatch("export_design", {"design_id": "SYNTH-DESIGN-0001"})
    assert result["source"] == "live"
