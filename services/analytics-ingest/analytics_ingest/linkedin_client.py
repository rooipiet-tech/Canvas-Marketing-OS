"""analytics_ingest.linkedin_client — dual-mode LinkedIn Community Management connector.

Uses THREE new, distinct Key Vault secret names in addition to the existing
`linkedin-analytics-client-secret` (which alone gates `is_linkedin_analytics_live_mode()`,
same posture as ga4_client/search_console_client — the primary secret is the
dual-mode switch; the rest are additional live-mode-only configuration that
raises rather than silently falling back to fixtures when absent, AC-25/AC-27):

  * `linkedin-analytics-client-id`      — the LinkedIn developer app's client id.
  * `linkedin-analytics-refresh-token`  — minted by a one-time 3-legged OAuth2
    consent (same shape as mcp-canva's `scripts/oauth_consent.py` flow — a
    LinkedIn app's Community Management API product requires member
    authorization, not just a client id/secret, so an authorization-code grant
    has to happen once before any refresh token exists).
  * `linkedin-analytics-org-urn`        — e.g. `urn:li:organization:12345678`.

Distinct from `linkedin-client-secret` (entry 6 in docs/credentials-runbook.md,
scoped to campaign execution/social publishing, not analytics) and from the
pre-existing `linkedin-analytics-client-secret` (entry 10) — AC-25's
separation requirement, preserved exactly as the fixture-era module
established it.

Falls back to its bundled fixture JSON (tests/fixtures/linkedin_<day>.json)
whenever `is_linkedin_analytics_live_mode()` is False, and skips live calls
cleanly (no exception) when the secret is absent (AC-27). The fixture path is
BYTE-EQUIVALENT to what it was before this change: still selected whenever
`linkedin-analytics-client-secret` is absent, still reads the same file,
still returns [] for a missing fixture.

TWO NON-CODE PREREQUISITES, same shape as GA4/Search Console's:
  1. The LinkedIn developer app must have the Community Management API
     product approved (a manual LinkedIn review, not a config change), with
     the `r_organization_social` scope.
  2. A person with admin access to the organization page must complete the
     3-legged OAuth consent once to mint the refresh token — there is no
     client-credentials grant for this API.

RESPONSE SHAPE IS ASSUMED, NOT LIVE-VERIFIED. Unlike GA4/Search Console
(built directly against Google's public REST reference), no live LinkedIn
credential existed in this session to introspect the real Posts API /
organizationalEntityShareStatistics response against. Two assumptions in
particular are best-effort and must be confirmed on the first real call
(record the outcome in docs/architecture/19-live-verification-log.md,
mirroring buffer_client.py's own ASSUMED_METRIC_FIELDS precedent and its
later live-introspection correction):

  * Field names below (`createdAt`, `commentary`, `impressionCount`,
    `clickCount`, `likeCount`, `commentCount`, `shareCount`) are LinkedIn's
    documented names as of this writing, not independently confirmed here.
  * `organizationalEntityShareStatistics`'s `elements` list is ASSUMED to
    come back in the same order as the requested `shares[i]` params, so
    `_fetch_share_statistics` correlates positionally rather than by a
    per-element URN field — getting this wrong would silently mismatch
    metrics onto the wrong post rather than fail loudly (the exact shape of
    mistake Buffer's own B3 live-verification check exists to catch).

Post archetype / campaign attribution mirrors the fallback
mcp-buffer/app/dispatch.py's create_draft documents for Buffer: LinkedIn's
Posts API has the identical "no metadata field round-trips" shape, so the
CTA URL already embedded in the post's `commentary` text at
content-authoring time (utm_campaign/utm_content query params) is the one
thing this connector can read attribution back from — see `_extract_utm`.

L-0074 applies in full: a resolvable `linkedin-analytics-client-secret` in
Key Vault flips this connector live with no code or config change. That
correctness is exactly why the live path must be independently verified
against a real LinkedIn org before anyone trusts a nightly run's LinkedIn
KPIs — this module has never made a real call to LinkedIn's API.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from analytics_ingest.credentials import is_live_mode, resolve_secret

LINKEDIN_ANALYTICS_CLIENT_ID_ENV = "LINKEDIN_ANALYTICS_CLIENT_ID"
LINKEDIN_ANALYTICS_CLIENT_ID_SECRET = "linkedin-analytics-client-id"
LINKEDIN_ANALYTICS_CLIENT_SECRET_ENV = "LINKEDIN_ANALYTICS_CLIENT_SECRET"
LINKEDIN_ANALYTICS_CLIENT_SECRET_SECRET = "linkedin-analytics-client-secret"
LINKEDIN_ANALYTICS_REFRESH_TOKEN_ENV = "LINKEDIN_ANALYTICS_REFRESH_TOKEN"
LINKEDIN_ANALYTICS_REFRESH_TOKEN_SECRET = "linkedin-analytics-refresh-token"
LINKEDIN_ANALYTICS_ORG_URN_ENV = "LINKEDIN_ANALYTICS_ORG_URN"
LINKEDIN_ANALYTICS_ORG_URN_SECRET = "linkedin-analytics-org-urn"

LINKEDIN_OAUTH_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
LINKEDIN_API_BASE = "https://api.linkedin.com/rest"
_REQUEST_TIMEOUT_SECONDS = 60.0

# LinkedIn's REST APIs are calendar-versioned (YYYYMM) via this header --
# there is no "latest" default. UNVERIFIED against LinkedIn's live
# Versioning page — confirm and bump before the first real nightly run
# (see this module's docstring and 19-live-verification-log.md).
LINKEDIN_API_VERSION = "202502"

_POSTS_PAGE_SIZE = 50
_MAX_PAGES = 5
# LinkedIn's documented cap on the `shares` finder parameter for
# organizationalEntityShareStatistics — ASSUMED, see module docstring.
_MAX_SHARES_PER_STATS_CALL = 20

_FIXTURES_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
_URL_RE = re.compile(r"https?://\S+")


class LinkedInClientError(RuntimeError):
    """Live LinkedIn call failed or was misconfigured."""


def is_linkedin_analytics_live_mode() -> bool:
    return is_live_mode(
        LINKEDIN_ANALYTICS_CLIENT_SECRET_ENV, LINKEDIN_ANALYTICS_CLIENT_SECRET_SECRET
    )


def linkedin_org_urn() -> str | None:
    return resolve_secret(LINKEDIN_ANALYTICS_ORG_URN_ENV, LINKEDIN_ANALYTICS_ORG_URN_SECRET)


def linkedin_api_base() -> str:
    """Overridable for tests, mirroring buffer_client.buffer_api_url()."""
    return os.environ.get("LINKEDIN_API_BASE", LINKEDIN_API_BASE).rstrip("/")


def linkedin_oauth_token_url() -> str:
    return os.environ.get("LINKEDIN_OAUTH_TOKEN_URL", LINKEDIN_OAUTH_TOKEN_URL)


def linkedin_api_version() -> str:
    return os.environ.get("LINKEDIN_API_VERSION", LINKEDIN_API_VERSION)


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "LinkedIn-Version": linkedin_api_version(),
        "X-Restli-Protocol-Version": "2.0.0",
    }


def _access_token(client: httpx.Client) -> str:
    """Exchange the stored refresh token for a short-lived access token.

    No client-credentials grant exists for this API (see module
    docstring) — a refresh token minted by a one-time human OAuth consent
    is required in addition to the client id/secret pair.
    """
    client_id = resolve_secret(
        LINKEDIN_ANALYTICS_CLIENT_ID_ENV, LINKEDIN_ANALYTICS_CLIENT_ID_SECRET
    )
    client_secret = resolve_secret(
        LINKEDIN_ANALYTICS_CLIENT_SECRET_ENV, LINKEDIN_ANALYTICS_CLIENT_SECRET_SECRET
    )
    refresh_token = resolve_secret(
        LINKEDIN_ANALYTICS_REFRESH_TOKEN_ENV, LINKEDIN_ANALYTICS_REFRESH_TOKEN_SECRET
    )
    missing = [
        name
        for name, value in (
            ("LINKEDIN_ANALYTICS_CLIENT_ID", client_id),
            ("LINKEDIN_ANALYTICS_REFRESH_TOKEN", refresh_token),
        )
        if not value
    ]
    if missing:
        raise LinkedInClientError(
            "LinkedIn analytics live mode requires " + " and ".join(missing) + " "
            "(Key Vault: linkedin-analytics-client-id / linkedin-analytics-refresh-token) "
            "in addition to linkedin-analytics-client-secret — refusing to fall back to "
            "fixtures, which would silently feed fake rows into a real rollup"
        )

    response = client.post(
        linkedin_oauth_token_url(),
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        },
    )
    if response.status_code != 200:
        raise LinkedInClientError(
            f"LinkedIn OAuth token refresh failed: HTTP {response.status_code} "
            f"{response.text[:400]}"
        )
    token = response.json().get("access_token")
    if not token:
        raise LinkedInClientError("LinkedIn OAuth token refresh returned no access_token")
    return str(token)


def _day_bounds_ms(day: str) -> tuple[int, int]:
    """UTC midnight-to-midnight window for `day` (YYYY-MM-DD), in epoch
    milliseconds — the unit LinkedIn's `createdAt` field uses."""
    start = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def _extract_utm(text: str | None) -> tuple[str | None, str | None]:
    """(utm_campaign, utm_content) parsed out of the first URL in `text`.

    Mirrors the fallback mcp-buffer/app/dispatch.py documents for Buffer:
    the CTA URL embedded in the post's own text at content-authoring time
    is the one thing this connector can read attribution back from, since
    LinkedIn's Posts API (like Buffer's GraphQL schema) exposes no
    metadata field that both accepts a free-form label on create and
    returns it on read.
    """
    if not text:
        return None, None
    match = _URL_RE.search(text)
    if not match:
        return None, None
    query = parse_qs(urlparse(match.group(0)).query)
    campaign = (query.get("utm_campaign") or [None])[0]
    content = (query.get("utm_content") or [None])[0]
    return campaign, content


def _fetch_posts_for_day(
    client: httpx.Client, token: str, org_urn: str, day: str
) -> list[dict[str, Any]]:
    """List the org's posts, paging until a short page, filtered to `day`."""
    start_ms, end_ms = _day_bounds_ms(day)
    matched: list[dict[str, Any]] = []
    start = 0
    for _ in range(_MAX_PAGES):
        response = client.get(
            f"{linkedin_api_base()}/posts",
            params={"author": org_urn, "q": "author", "count": _POSTS_PAGE_SIZE, "start": start},
            headers=_headers(token),
        )
        if response.status_code != 200:
            raise LinkedInClientError(
                f"LinkedIn posts listing failed: HTTP {response.status_code} {response.text[:400]}"
            )
        elements = response.json().get("elements") or []
        for element in elements:
            created_at = element.get("createdAt")
            if isinstance(created_at, int) and start_ms <= created_at < end_ms:
                matched.append(element)
        if len(elements) < _POSTS_PAGE_SIZE:
            break
        start += _POSTS_PAGE_SIZE
    return matched


def _chunk(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _fetch_share_statistics(
    client: httpx.Client, token: str, org_urn: str, post_urns: list[str]
) -> dict[str, dict[str, Any]]:
    """Map post URN -> totalShareStatistics dict, batched to the
    (ASSUMED, see module docstring) 20-share-per-call cap, correlated
    positionally against the requested batch order."""
    stats_by_urn: dict[str, dict[str, Any]] = {}
    for batch in _chunk(post_urns, _MAX_SHARES_PER_STATS_CALL):
        params = [("q", "organizationalEntity"), ("organizationalEntity", org_urn)]
        params.extend((f"shares[{i}]", urn) for i, urn in enumerate(batch))
        response = client.get(
            f"{linkedin_api_base()}/organizationalEntityShareStatistics",
            params=params,
            headers=_headers(token),
        )
        if response.status_code != 200:
            raise LinkedInClientError(
                f"LinkedIn share statistics call failed: HTTP {response.status_code} "
                f"{response.text[:400]}"
            )
        elements = response.json().get("elements") or []
        for urn, element in zip(batch, elements):
            stats_by_urn[urn] = element.get("totalShareStatistics") or {}
    return stats_by_urn


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def get_linkedin_day_live(day: str) -> list[dict[str, Any]]:
    """Fetch one day of LinkedIn organization post-performance rows."""
    org_urn = linkedin_org_urn()
    if not org_urn:
        raise LinkedInClientError(
            "LinkedIn analytics live mode requires LINKEDIN_ANALYTICS_ORG_URN "
            "(Key Vault: linkedin-analytics-org-urn) — refusing to fall back to "
            "fixtures, which would silently feed fake rows into a real rollup"
        )

    with httpx.Client(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
        token = _access_token(client)
        posts = _fetch_posts_for_day(client, token, org_urn, day)
        post_urns = [post["id"] for post in posts if post.get("id")]
        stats_by_urn = _fetch_share_statistics(client, token, org_urn, post_urns)

        rows: list[dict[str, Any]] = []
        for post in posts:
            urn = post.get("id")
            if not urn:
                continue
            stats = stats_by_urn.get(urn, {})
            utm_campaign, post_archetype = _extract_utm(post.get("commentary"))
            rows.append(
                {
                    "post_id": urn,
                    "post_archetype": post_archetype,
                    "impressions": _as_int(stats.get("impressionCount")),
                    "clicks": _as_int(stats.get("clickCount")),
                    "reactions": _as_int(stats.get("likeCount")),
                    "comments": _as_int(stats.get("commentCount")),
                    "shares": _as_int(stats.get("shareCount")),
                    "utm_campaign": utm_campaign,
                }
            )
        return rows


def get_linkedin_day(day: str) -> list[dict[str, Any]]:
    """Return LinkedIn rows for `day` (YYYY-MM-DD).

    Live mode (a resolvable linkedin-analytics-client-secret) calls the
    Community Management Posts API + organizationalEntityShareStatistics
    (UNVERIFIED against a real LinkedIn org — see module docstring);
    otherwise reads tests/fixtures/linkedin_<day>.json, returning [] when
    that fixture does not exist.
    """
    if is_linkedin_analytics_live_mode():
        return get_linkedin_day_live(day)

    fixture_path = _FIXTURES_DIR / f"linkedin_{day}.json"
    if not fixture_path.exists():
        return []
    return json.loads(fixture_path.read_text(encoding="utf-8"))
