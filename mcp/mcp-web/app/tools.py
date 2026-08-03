"""mcp-web tools — fetch_url with an egress allow-list (AC-17).

Dual-mode gate: MCP_WEB_LIVE_MODE, a non-secret feature flag (orchestrator
-approved waiver, .loop/spec.json amendments v2, addressing plan-reviewer
finding F3 on plan_version 1) — mcp-web has no vendor credential (it is a
fetch+rate-limit server, not a Buffer/Canva-style integration), so its
fixture-vs-live switch cannot be a Key-Vault-backed secret env var like
mcp-buffer/mcp-canva's. mcp-buffer and mcp-canva are unaffected by this
waiver and keep real secret-presence gating.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlparse

from mcp_common import flag_enabled

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"


class AllowlistViolation(Exception):
    """Raised when a fetch_url call targets a non-allow-listed host —
    always raised before any network call is attempted (AC-17)."""


def _allowlist() -> set[str]:
    raw = os.environ.get("MCP_WEB_ALLOWLIST", "example.com,api.example.com")
    return {h.strip().lower() for h in raw.split(",") if h.strip()}


def check_allowlist(url: str) -> None:
    """Raise AllowlistViolation if url's host isn't allow-listed. Called
    before the one real network access fetch_url ever makes (the live-mode
    GET below) — not called at all in fixture mode, which never touches
    the network in the first place (see fetch_url's own incident note)."""
    host = (urlparse(url).hostname or "").lower()
    if host not in _allowlist():
        raise AllowlistViolation(f"host not allow-listed: {host!r}")


def _load_fixture(name: str) -> dict:
    path = FIXTURES_DIR / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def fetch_url(arguments: dict, *, http_client=None) -> dict:
    """fetch_url tool implementation.

    Fixture mode (MCP_WEB_LIVE_MODE unset/falsy, the default — AC-4/AC-7 —
    or mcp_common.credentials.force_fixture_mode()'s per-request override):
    returns the checked-in synthetic fixture, no network call performed at
    all, checked BEFORE the allow-list guard. Live mode (MCP_WEB_LIVE_MODE
    truthy and no override): performs a real GET, but only for
    allow-listed hosts (check_allowlist below always runs first in this
    branch — immediately before the one real network call this function
    ever makes).

    INCIDENT (2026-08-02, live — caj-mcp-smoke, deploy-mcp #27): with
    check_allowlist() running unconditionally before the fixture-mode
    check, test_conformance.py's synthetic `example.com` argument tripped
    it the moment ca-mcp-web's live MCP_WEB_ALLOWLIST diverged from the
    code's own example.com-inclusive default — even with PR #53's
    force_fixture_mode() override correctly active, the allow-list guard
    ran first and rejected the request before fixture mode ever got a
    chance to short-circuit. AC-17's guarantee ("rejected before any
    network call is attempted", test_web_allowlist.py) is about protecting
    the real GET below; it protects nothing when fixture mode guarantees
    no network call happens at all — and test_web_allowlist.py's own tests
    only ever exercise this guard with MCP_WEB_LIVE_MODE=true, confirming
    fixture mode was never meant to be gated by it. Checking fixture mode
    first restores force_fixture_mode()'s intended guarantee: fixture mode
    is fully deterministic and config-independent, immune to whatever the
    live allow-list happens to contain.
    """
    url = arguments.get("url", "")

    if not flag_enabled("MCP_WEB_LIVE_MODE"):
        fixture = _load_fixture("fetch_url")
        return {"source": "fixture", "url": url, **fixture}

    check_allowlist(url)

    import httpx

    client = http_client if http_client is not None else httpx
    response = client.get(url, timeout=10.0)
    return {
        "source": "live",
        "url": url,
        "status_code": response.status_code,
        "body": response.text[:4096],
    }
