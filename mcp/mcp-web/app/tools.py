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
    before any network access is attempted, in both fixture and live
    mode."""
    host = (urlparse(url).hostname or "").lower()
    if host not in _allowlist():
        raise AllowlistViolation(f"host not allow-listed: {host!r}")


def _load_fixture(name: str) -> dict:
    path = FIXTURES_DIR / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def fetch_url(arguments: dict, *, http_client=None) -> dict:
    """fetch_url tool implementation.

    Fixture mode (MCP_WEB_LIVE_MODE unset/falsy, the default — AC-4/AC-7):
    returns the checked-in synthetic fixture, no network call performed at
    all. Live mode (MCP_WEB_LIVE_MODE truthy): performs a real GET, but
    only for allow-listed hosts (the allow-list check below always runs
    first, in both modes).
    """
    url = arguments.get("url", "")
    check_allowlist(url)

    if not flag_enabled("MCP_WEB_LIVE_MODE"):
        fixture = _load_fixture("fetch_url")
        return {"source": "fixture", "url": url, **fixture}

    import httpx

    client = http_client if http_client is not None else httpx
    response = client.get(url, timeout=10.0)
    return {
        "source": "live",
        "url": url,
        "status_code": response.status_code,
        "body": response.text[:4096],
    }
