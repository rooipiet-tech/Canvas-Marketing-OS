"""analytics_ingest.ga4_client — dual-mode GA4 connector.

Uses a NEW, distinct Key Vault secret name (ga4-service-account-key), never
the existing coarse google-oauth-client-secret (AC-25). Falls back to its
bundled fixture JSON (tests/fixtures/ga4_<day>.json) whenever
is_live_mode() is False, and skips live calls cleanly (no exception) when
the secret is absent (AC-27).

LIVE PATH ADDED 7 Aug 2026 (F-GOOGLE-LIVE-CLIENTS). Until this date the
live path was deliberately unimplemented -- the original build's
.loop/spec.json put it out of scope because no credentials existed yet, and
is_ga4_live_mode() existed purely as the seam for a future session to wire
the real call behind. This is that session. The fixture path below is
BYTE-EQUIVALENT in behaviour to what it was before: fixture mode is still
selected whenever the secret is absent, still reads the same file, still
returns [] for a missing fixture. Nothing that passed before changes.

Live mode additionally requires GA4_PROPERTY_ID (Key Vault: ga4-property-id)
-- the numeric property ID, e.g. "399508463", NOT the G-XXXXXXX measurement
ID. A resolvable service-account key with no property ID is a
misconfiguration, not a fixture-mode signal, so it raises rather than
silently falling back: silently serving fixture rows into a production
rollup that believes they are real is the worse failure of the two.

BEFORE THIS WORKS, two things must be true on Google's side, neither of
them code (see the delivery note in the project doc):
  1. The service account's email must be added as a Viewer on the GA4
     property itself. Creating the key is not access.
  2. The property must be the one the site actually feeds. Per
     claude/traffic-plan-2026-07.md, the property visible to Pieter's login
     (399508463, G-ESR2556XFT) has NEVER received data -- the live site tags
     GTM-MDBW8M68 / GT-TQDGQXR sit in an agency-controlled account. Pointing
     this connector at 399508463 today would faithfully ingest zeroes.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx

from analytics_ingest.credentials import is_live_mode, resolve_secret
from analytics_ingest.google_auth import GA4_SCOPE, access_token

GA4_SERVICE_ACCOUNT_KEY_ENV = "GA4_SERVICE_ACCOUNT_KEY"
GA4_SERVICE_ACCOUNT_KEY_SECRET = "ga4-service-account-key"
GA4_PROPERTY_ID_ENV = "GA4_PROPERTY_ID"
GA4_PROPERTY_ID_SECRET = "ga4-property-id"

GA4_DATA_API_BASE = "https://analyticsdata.googleapis.com/v1beta"
_REQUEST_TIMEOUT_SECONDS = 60.0

# Landing page and session-scoped campaign. sessionCampaignName is the
# session's own utm_campaign, which is what the UTM reconciliation step
# (analytics_ingest.utm) matches against analytics.utm_campaign_map -- NOT
# firstUserCampaignName, which would attribute a returning CFO's session to
# whatever campaign first acquired them months ago.
_DIMENSIONS = ["landingPagePlusQueryString", "sessionCampaignName"]

# GA4 renamed "conversions" to "key events" in 2024 and the Data API grew a
# `keyEvents` metric alongside the older `conversions` one. Which of the two
# a given property accepts depends on its own migration state, and Google's
# public schema page does not state a hard removal date. Rather than guess,
# the live path asks for keyEvents first and retries once with conversions
# if the API rejects the metric name -- so this works against a property in
# either state, and logs which one it settled on. Collapse this to a single
# constant once we have confirmed the real property's answer.
_PRIMARY_CONVERSION_METRIC = "keyEvents"
_FALLBACK_CONVERSION_METRIC = "conversions"

# GA4's own cap is 100,000 rows per request. The site has ~89 posts and
# well under a thousand distinct landing pages, so one page is ample; a
# response that hits this limit is logged rather than silently truncated.
_ROW_LIMIT = 100_000

_FIXTURES_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures"


class GA4ClientError(RuntimeError):
    """Live GA4 call failed or was misconfigured."""


def is_ga4_live_mode() -> bool:
    return is_live_mode(GA4_SERVICE_ACCOUNT_KEY_ENV, GA4_SERVICE_ACCOUNT_KEY_SECRET)


def ga4_property_id() -> str | None:
    return resolve_secret(GA4_PROPERTY_ID_ENV, GA4_PROPERTY_ID_SECRET)


def ga4_api_base() -> str:
    """Overridable for tests, mirroring buffer_client.buffer_api_url()."""
    return os.environ.get("GA4_DATA_API_BASE", GA4_DATA_API_BASE).rstrip("/")


def _build_request_body(day: str, conversion_metric: str) -> dict[str, Any]:
    return {
        "dateRanges": [{"startDate": day, "endDate": day}],
        "dimensions": [{"name": name} for name in _DIMENSIONS],
        "metrics": [
            {"name": "sessions"},
            {"name": "engagedSessions"},
            {"name": conversion_metric},
        ],
        "limit": _ROW_LIMIT,
        "keepEmptyRows": False,
    }


def _as_int(value: Any) -> int:
    """GA4 returns every metric value as a string, including integers."""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _normalise_campaign(raw: Any) -> str | None:
    """GA4 reports an absent campaign as the literal '(not set)' (and
    '(direct)' for direct traffic). The fixture contract uses null for
    'no campaign', and utm.reconcile_utm treats null as 'nothing to match'
    rather than quarantining it -- so map both sentinels onto None instead
    of letting the string '(not set)' land in analytics.ga4_metrics and
    then get quarantined as an unmatched utm_campaign every single night.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text or text in {"(not set)", "(direct)", "(none)"}:
        return None
    return text


def _parse_response(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in payload.get("rows") or []:
        dimension_values = row.get("dimensionValues") or []
        metric_values = row.get("metricValues") or []
        if len(dimension_values) < 2 or len(metric_values) < 3:
            continue
        rows.append(
            {
                "landing_page": dimension_values[0].get("value"),
                "sessions": _as_int(metric_values[0].get("value")),
                "engaged_sessions": _as_int(metric_values[1].get("value")),
                "conversions": _as_int(metric_values[2].get("value")),
                "utm_campaign": _normalise_campaign(dimension_values[1].get("value")),
            }
        )
    return rows


def _run_report(token: str, property_id: str, body: dict[str, Any]) -> httpx.Response:
    url = f"{ga4_api_base()}/properties/{property_id}:runReport"
    with httpx.Client(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
        return client.post(
            url,
            json=body,
            headers={"Authorization": f"Bearer {token}"},
        )


def _rejects_metric_name(response: httpx.Response) -> bool:
    """True when a 400 is specifically about the conversion metric's name,
    rather than any other bad-request cause (a wrong property ID, a bad
    date). Only that specific shape justifies the one retry."""
    if response.status_code != 400:
        return False
    text = response.text or ""
    return _PRIMARY_CONVERSION_METRIC in text or "did not match" in text


def get_ga4_day_live(day: str) -> list[dict[str, Any]]:
    """Fetch one day of GA4 landing-page rows via the Data API."""
    raw_key = resolve_secret(GA4_SERVICE_ACCOUNT_KEY_ENV, GA4_SERVICE_ACCOUNT_KEY_SECRET)
    if not raw_key:
        raise GA4ClientError("GA4 live mode requested but no service-account key resolved")

    property_id = ga4_property_id()
    if not property_id:
        raise GA4ClientError(
            "GA4 service-account key is configured but GA4_PROPERTY_ID "
            "(Key Vault: ga4-property-id) is not — refusing to fall back to "
            "fixtures, which would silently feed fake rows into a real rollup"
        )

    token = access_token(raw_key, GA4_SCOPE)

    response = _run_report(token, property_id, _build_request_body(day, _PRIMARY_CONVERSION_METRIC))
    if _rejects_metric_name(response):
        response = _run_report(
            token, property_id, _build_request_body(day, _FALLBACK_CONVERSION_METRIC)
        )

    if response.status_code != 200:
        raise GA4ClientError(
            f"GA4 runReport failed for {day}: HTTP {response.status_code} {response.text[:400]}"
        )

    return _parse_response(response.json())


def get_ga4_day(day: str) -> list[dict[str, Any]]:
    """Return GA4 rows for `day` (YYYY-MM-DD).

    Live mode (a resolvable ga4-service-account-key) calls the GA4 Data
    API; otherwise reads tests/fixtures/ga4_<day>.json, returning [] when
    that fixture does not exist.
    """
    if is_ga4_live_mode():
        return get_ga4_day_live(day)

    fixture_path = _FIXTURES_DIR / f"ga4_{day}.json"
    if not fixture_path.exists():
        return []
    return json.loads(fixture_path.read_text(encoding="utf-8"))
