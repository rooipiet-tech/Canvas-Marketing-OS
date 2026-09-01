"""AC-17: mcp-web's fetch tool enforces an egress allow-list: requests to
non-allow-listed hosts are rejected before any network call is attempted;
allow-listed hosts proceed.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.mcp_web_allowlist


class _RecordingClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get(self, url, timeout=None):
        self.calls.append(url)

        class _Response:
            status_code = 200
            text = "ok"

        return _Response()


def test_allowlisted_host_not_rejected(monkeypatch, server_app):
    monkeypatch.setenv("MCP_WEB_ALLOWLIST", "example.com")
    monkeypatch.setenv("MCP_WEB_LIVE_MODE", "true")

    module = server_app("mcp-web")
    client = _RecordingClient()

    result = module.fetch_url({"url": "https://example.com/synthetic"}, http_client=client)

    assert result["source"] == "live"
    assert len(client.calls) == 1


def test_non_allowlisted_host_rejected_before_any_network_call(monkeypatch, server_app):
    monkeypatch.setenv("MCP_WEB_ALLOWLIST", "example.com")
    monkeypatch.setenv("MCP_WEB_LIVE_MODE", "true")

    module = server_app("mcp-web")
    client = _RecordingClient()

    with pytest.raises(module.AllowlistViolation):
        module.fetch_url(
            {"url": "https://not-allowlisted.example.net/synthetic"}, http_client=client
        )

    assert client.calls == []


class _LongBodyClient:
    """Returns a body far larger than any cap, so a test can assert where
    truncation actually lands."""

    def __init__(self, length: int = 50_000) -> None:
        self.length = length

    def get(self, url, timeout=None):
        length = self.length

        class _Response:
            status_code = 200
            text = "x" * length

        return _Response()


def test_live_body_is_capped_at_the_default_budget(monkeypatch, server_app):
    """F-INGEST-EVIDENCE-WINDOW: this cap is the BINDING one for function
    09's market scan -- the orchestrator applies its own per-source budget
    on top, so whatever this returns is the ceiling on how much real
    evidence a signal can ever be attributed to."""
    monkeypatch.setenv("MCP_WEB_ALLOWLIST", "example.com")
    monkeypatch.setenv("MCP_WEB_LIVE_MODE", "true")
    monkeypatch.delenv("MCP_WEB_MAX_BODY_CHARS", raising=False)

    module = server_app("mcp-web")
    # app.main re-exports the tool, not its budget constant; server_app has
    # already imported app.tools as a side effect of loading app.main.
    import app.tools as web_tools

    result = module.fetch_url({"url": "https://example.com/long"}, http_client=_LongBodyClient())

    assert len(result["body"]) == web_tools.DEFAULT_MAX_BODY_CHARS
    assert web_tools.DEFAULT_MAX_BODY_CHARS > 4096  # the previous hardcoded cap


def test_body_cap_is_tunable_from_the_environment(monkeypatch, server_app):
    """Read at call time, not import time (CO-1, same convention as
    rate_limiter.py's ceiling), so a deployment can tune it without a
    code change."""
    monkeypatch.setenv("MCP_WEB_ALLOWLIST", "example.com")
    monkeypatch.setenv("MCP_WEB_LIVE_MODE", "true")
    monkeypatch.setenv("MCP_WEB_MAX_BODY_CHARS", "500")

    module = server_app("mcp-web")

    result = module.fetch_url({"url": "https://example.com/long"}, http_client=_LongBodyClient())

    assert len(result["body"]) == 500
