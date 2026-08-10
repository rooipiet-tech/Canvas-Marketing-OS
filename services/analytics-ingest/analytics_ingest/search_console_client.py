"""analytics_ingest.search_console_client — dual-mode Search Console connector.

Uses a NEW, distinct Key Vault secret name
(search-console-service-account-key), never the existing coarse
google-oauth-client-secret (AC-25). Falls back to its bundled fixture JSON
(tests/fixtures/search_console_<day>.json) whenever is_live_mode() is
False, and skips live calls cleanly (no exception) when the secret is
absent (AC-27).

LIVE PATH ADDED 7 Aug 2026 (F-GOOGLE-LIVE-CLIENTS) — see ga4_client's
module docstring for the identical rationale and the identical
fixture-behaviour-preservation guarantee.

Live mode additionally requires SEARCH_CONSOLE_SITE_URL (Key Vault:
search-console-site-url) — the property exactly as Search Console lists it,
which is either a URL-prefix property ("https://canvasintelligence.com/",
trailing slash included) or a domain property ("sc-domain:canvasintelligence.com").
Getting this string wrong returns 403, not an empty result, so it is worth
copying out of the Search Console UI rather than typing.

TWO NON-CODE PREREQUISITES, same shape as GA4's:
  1. The service account's email must be added as a user on the Search
     Console property (Settings -> Users and permissions -> Add user,
     Restricted is enough for read).
  2. Per claude/traffic-plan-2026-07.md, Pieter's Google login currently has
     ZERO Search Console properties — the property has to be claimed or
     handed over before there is anything to authorise. That is an access
     errand, not a code gap, and it blocks this connector regardless of
     what the key says.

DATA FRESHNESS: Search Console finalises a day's data on a lag, commonly
2-3 days. A nightly run for "yesterday" will therefore often see partial
rows and, run again later, see different numbers. `dataState` is left at
Google's default ("final") so this connector reports only settled data and
simply returns fewer rows for a too-recent day, rather than ingesting
provisional numbers that would later contradict themselves in a rollup that
inserts ON CONFLICT DO NOTHING and so would never correct them.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from analytics_ingest.credentials import is_live_mode, resolve_secret
from analytics_ingest.google_auth import SEARCH_CONSOLE_SCOPE, access_token

SEARCH_CONSOLE_SERVICE_ACCOUNT_KEY_ENV = "SEARCH_CONSOLE_SERVICE_ACCOUNT_KEY"
SEARCH_CONSOLE_SERVICE_ACCOUNT_KEY_SECRET = "search-console-service-account-key"
SEARCH_CONSOLE_SITE_URL_ENV = "SEARCH_CONSOLE_SITE_URL"
SEARCH_CONSOLE_SITE_URL_SECRET = "search-console-site-url"

SEARCH_CONSOLE_API_BASE = "https://www.googleapis.com/webmasters/v3"
_REQUEST_TIMEOUT_SECONDS = 60.0

# Row order matches the fixture's natural_row_id construction in
# ingest.ingest_search_console_day: f"{page}::{query}".
_DIMENSIONS = ["query", "page"]

# Google's per-request maximum. The API pages via startRow; this connector
# pages until a short page comes back, so a site that ever exceeds 25k
# query/page pairs in a day is still ingested completely.
_ROW_LIMIT = 25_000
_MAX_PAGES = 20

_FIXTURES_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures"


class SearchConsoleClientError(RuntimeError):
    """Live Search Console call failed or was misconfigured."""


def is_search_console_live_mode() -> bool:
    return is_live_mode(
        SEARCH_CONSOLE_SERVICE_ACCOUNT_KEY_ENV, SEARCH_CONSOLE_SERVICE_ACCOUNT_KEY_SECRET
    )


def search_console_site_url() -> str | None:
    return resolve_secret(SEARCH_CONSOLE_SITE_URL_ENV, SEARCH_CONSOLE_SITE_URL_SECRET)


def search_console_api_base() -> str:
    """Overridable for tests, mirroring buffer_client.buffer_api_url()."""
    return os.environ.get("SEARCH_CONSOLE_API_BASE", SEARCH_CONSOLE_API_BASE).rstrip("/")


def _as_int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _parse_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in payload.get("rows") or []:
        keys = row.get("keys") or []
        if len(keys) < 2:
            continue
        rows.append(
            {
                "query": keys[0],
                "page": keys[1],
                "clicks": _as_int(row.get("clicks")),
                "impressions": _as_int(row.get("impressions")),
                "ctr": _as_float(row.get("ctr")),
                "position": _as_float(row.get("position")),
            }
        )
    return rows


def get_search_console_day_live(day: str) -> list[dict[str, Any]]:
    """Fetch one day of Search Console query/page rows, paging to exhaustion."""
    raw_key = resolve_secret(
        SEARCH_CONSOLE_SERVICE_ACCOUNT_KEY_ENV, SEARCH_CONSOLE_SERVICE_ACCOUNT_KEY_SECRET
    )
    if not raw_key:
        raise SearchConsoleClientError(
            "Search Console live mode requested but no service-account key resolved"
        )

    site_url = search_console_site_url()
    if not site_url:
        raise SearchConsoleClientError(
            "Search Console service-account key is configured but "
            "SEARCH_CONSOLE_SITE_URL (Key Vault: search-console-site-url) is not "
            "— refusing to fall back to fixtures, which would silently feed fake "
            "rows into a real rollup"
        )

    token = access_token(raw_key, SEARCH_CONSOLE_SCOPE)
    url = f"{search_console_api_base()}/sites/{quote(site_url, safe='')}/searchAnalytics/query"

    collected: list[dict[str, Any]] = []
    with httpx.Client(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
        for page_index in range(_MAX_PAGES):
            response = client.post(
                url,
                json={
                    "startDate": day,
                    "endDate": day,
                    "dimensions": _DIMENSIONS,
                    "type": "web",
                    "rowLimit": _ROW_LIMIT,
                    "startRow": page_index * _ROW_LIMIT,
                },
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.status_code != 200:
                raise SearchConsoleClientError(
                    f"Search Console query failed for {day}: "
                    f"HTTP {response.status_code} {response.text[:400]}"
                )

            page_rows = _parse_rows(response.json())
            collected.extend(page_rows)
            if len(page_rows) < _ROW_LIMIT:
                break

    return collected


def get_search_console_day(day: str) -> list[dict[str, Any]]:
    """Return Search Console rows for `day` (YYYY-MM-DD).

    Live mode (a resolvable search-console-service-account-key) calls the
    Search Analytics API; otherwise reads
    tests/fixtures/search_console_<day>.json, returning [] when that
    fixture does not exist.
    """
    if is_search_console_live_mode():
        return get_search_console_day_live(day)

    fixture_path = _FIXTURES_DIR / f"search_console_{day}.json"
    if not fixture_path.exists():
        return []
    return json.loads(fixture_path.read_text(encoding="utf-8"))
