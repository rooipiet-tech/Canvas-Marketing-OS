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
