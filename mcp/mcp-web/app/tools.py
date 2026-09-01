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

import html
import json
import os
import re
from pathlib import Path
from urllib.parse import urlparse

from mcp_common import flag_enabled

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"

# Live-response body cap. Read from the environment at call time rather
# than hardcoded (CO-1, same convention as rate_limiter.py's ceiling), so
# a deployment can tune it without a code change.
#
# Raised from a hardcoded 4096 (F-INGEST-EVIDENCE-WINDOW): this cap is the
# BINDING one for function 09's market scan -- the orchestrator applies its
# own per-source budget on top, so whatever this returns is the ceiling on
# how much real evidence a signal can ever be attributed to. 3 of the 4
# URLs in the market-intelligence scan profile are RSS feeds, where the first few thousand
# characters are largely channel preamble rather than article text.
DEFAULT_MAX_BODY_CHARS = 16000


def _max_body_chars() -> int:
    raw = os.environ.get("MCP_WEB_MAX_BODY_CHARS")
    if raw is None or raw == "":
        return DEFAULT_MAX_BODY_CHARS
    return int(raw)


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


# ---------------------------------------------------------------------
# probe_url — the source-promotion sandbox
# ---------------------------------------------------------------------
#
# A candidate source cannot be evaluated without fetching it, and fetching
# it requires it to be on the egress allow-list -- which is the decision
# the evaluation exists to inform. That circularity is why source
# promotion needs a sandbox rather than a config flag.
#
# probe_url is that sandbox, and it is narrow in two independent ways:
#
#   1. A SEPARATE ALLOWANCE. It checks MCP_WEB_PROBE_ALLOWLIST, never the
#      production MCP_WEB_ALLOWLIST that fetch_url uses. A host being
#      probeable therefore grants it nothing in the scan path: the two
#      lists are different env vars, set from different sources, and a
#      candidate is promoted from one to the other only by a human
#      approving it (see the orchestrator's probe-sources handler).
#   2. METADATA ONLY. It never returns the response body. Callers get
#      shape -- status, content type, size, whether it parses as a feed,
#      how many items, how much extractable text, and up to five item
#      titles as evidence a human can eyeball. Unapproved third-party
#      content therefore cannot reach a model through this tool, which is
#      what stops "probe a candidate" from becoming an unreviewed
#      ingestion path in its own right.
#
# The five sample titles are the one place candidate content crosses the
# boundary at all. They exist because a probe that says "200, feed, 40
# items" cannot tell a reviewer whether those items are about their
# market, and approving a source blind would defeat the point of asking.

PROBE_SAMPLE_TITLES = 5
PROBE_BODY_LIMIT = 200_000

_PROBE_ITEM_RE = re.compile(r"<(item|entry)\b[^>]*>(.*?)</\1>", re.DOTALL | re.IGNORECASE)
_PROBE_TITLE_RE = re.compile(r"<title\b[^>]*>(.*?)</title>", re.DOTALL | re.IGNORECASE)
_PROBE_SCRIPT_STYLE_RE = re.compile(
    r"<(script|style)\b[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE
)
_PROBE_TAG_RE = re.compile(r"<[^>]*>")
_PROBE_CDATA_RE = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.DOTALL)


def _probe_allowlist() -> set[str]:
    raw = os.environ.get("MCP_WEB_PROBE_ALLOWLIST", "")
    return {h.strip().lower() for h in raw.split(",") if h.strip()}


def check_probe_allowlist(url: str) -> None:
    """Raise AllowlistViolation unless url's host is on the PROBE list.

    Deliberately not a fallback to the production allow-list: an empty
    probe list means nothing may be probed, which is the fail-closed
    default this codebase uses everywhere else."""
    host = (urlparse(url).hostname or "").lower()
    if host not in _probe_allowlist():
        raise AllowlistViolation(f"host not probe-allow-listed: {host!r}")


def _probe_text(raw: str) -> str:
    text = _PROBE_CDATA_RE.sub(r"\1", raw)
    text = _PROBE_TAG_RE.sub(" ", text)
    return " ".join(html.unescape(text).split())


def probe_url(arguments: dict, *, http_client=None) -> dict:
    """Report the SHAPE of a candidate source. Never its content.

    Fixture mode short-circuits first, for the same reason fetch_url's
    does (see its incident note): the guard protects the real network
    call, and in fixture mode there isn't one."""
    url = arguments.get("url", "")

    if not flag_enabled("MCP_WEB_LIVE_MODE"):
        return {
            "source": "fixture",
            "url": url,
            "status_code": 200,
            "content_type": "application/rss+xml",
            "is_feed": True,
            "item_count": 3,
            "extractable_chars": 512,
            "sample_titles": ["SYNTHETIC-TEST-DATA probe sample title"],
            "byte_length": 1024,
        }

    check_probe_allowlist(url)

    import httpx

    client = http_client if http_client is not None else httpx
    response = client.get(url, timeout=10.0)
    body = response.text[:PROBE_BODY_LIMIT]

    items = _PROBE_ITEM_RE.findall(body)
    titles: list[str] = []
    for _tag, block in items[:PROBE_SAMPLE_TITLES]:
        match = _PROBE_TITLE_RE.search(block)
        if match:
            title = _probe_text(match.group(1))
            if title:
                titles.append(title[:200])

    extractable = _probe_text(_PROBE_SCRIPT_STYLE_RE.sub(" ", body)) if not items else " ".join(
        _probe_text(block) for _tag, block in items
    )

    return {
        "source": "live",
        "url": url,
        "final_url": str(getattr(response, "url", url)),
        "status_code": response.status_code,
        "content_type": str(getattr(response, "headers", {}).get("content-type", "")),
        "is_feed": bool(items),
        "item_count": len(items),
        "extractable_chars": len(extractable),
        "sample_titles": titles,
        "byte_length": len(response.text),
    }


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
        "body": response.text[: _max_body_chars()],
    }
