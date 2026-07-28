"""AC-5: mcp-web's rate limiter caps a burst of tool calls at a configured
ceiling within a window, then allows calls again once the window elapses.
"""

from __future__ import annotations

import time

import pytest

pytestmark = pytest.mark.mcp_rate_limit


def test_mcp_web_rate_limit_caps_burst_then_recovers(monkeypatch, server_app):
    ceiling = 5
    window_seconds = 1.0
    extra = 3

    monkeypatch.setenv("RATE_LIMIT_MAX", str(ceiling))
    monkeypatch.setenv("RATE_LIMIT_WINDOW_SEC", str(window_seconds))

    module = server_app("mcp-web")

    succeeded = 0
    rejected = 0
    for _ in range(ceiling + extra):
        try:
            module.dispatch("fetch_url", {"url": "https://example.com/synthetic"})
            succeeded += 1
        except module.RateLimitExceeded:
            rejected += 1

    assert succeeded == ceiling
    assert rejected == extra

    time.sleep(window_seconds + 0.2)

    # Window elapsed — a subsequent call succeeds again (not rejected).
    module.dispatch("fetch_url", {"url": "https://example.com/synthetic"})
