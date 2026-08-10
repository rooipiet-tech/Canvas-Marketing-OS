"""F-GOOGLE-LIVE-CLIENTS (7 Aug 2026) — the GA4 and Search Console live paths.

Before this change both connectors were fixture-only stubs whose docstrings
said "Live mode is intentionally unimplemented", so a resolvable secret
changed nothing at all. These tests prove three things the old suite could
not: that live mode actually issues the right request, that the response is
mapped onto the SAME row shape the fixtures and ingest.py already agree on,
and — most importantly — that fixture mode is completely unchanged when no
secret is present, so CI stays green with no credentials anywhere (AC-27).

HTTP is intercepted with respx, matching conftest.py's own Vault-mocking
pattern, so the real httpx call path inside each client is exercised rather
than replaced. Only the service-account token exchange is monkeypatched —
that one genuinely cannot run without a real Google-signed private key.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx
import pytest
import respx
from analytics_ingest import ga4_client, search_console_client
from analytics_ingest.google_auth import GoogleAuthError, parse_service_account_key

FIXTURE_DAY = "2026-07-31"

GA4_BASE = "https://analyticsdata.googleapis.com/v1beta"
GSC_BASE = "https://www.googleapis.com/webmasters/v3"


@pytest.fixture
def fake_token(monkeypatch):
    """Both clients import access_token by name, so patch it per-module."""
    monkeypatch.setattr(ga4_client, "access_token", lambda *_a, **_k: "fake-access-token")
    monkeypatch.setattr(
        search_console_client, "access_token", lambda *_a, **_k: "fake-access-token"
    )


# ---------------------------------------------------------------------------
# Fixture mode is untouched — the regression guard that matters most
# ---------------------------------------------------------------------------


def test_ga4_fixture_mode_unchanged_when_no_secret(monkeypatch):
    monkeypatch.delenv("GA4_SERVICE_ACCOUNT_KEY", raising=False)
    monkeypatch.delenv("KEY_VAULT_URI", raising=False)
    rows = ga4_client.get_ga4_day(FIXTURE_DAY)
    assert len(rows) == 3
    assert rows[0]["landing_page"] == "/blog/fabric-production-proof"
    assert rows[1]["utm_campaign"] is None


def test_search_console_fixture_mode_unchanged_when_no_secret(monkeypatch):
    monkeypatch.delenv("SEARCH_CONSOLE_SERVICE_ACCOUNT_KEY", raising=False)
    monkeypatch.delenv("KEY_VAULT_URI", raising=False)
    rows = search_console_client.get_search_console_day(FIXTURE_DAY)
    assert len(rows) == 3
    assert rows[0]["query"] == "canvas marketing os"


def test_missing_fixture_still_returns_empty_list(monkeypatch):
    monkeypatch.delenv("GA4_SERVICE_ACCOUNT_KEY", raising=False)
    monkeypatch.delenv("KEY_VAULT_URI", raising=False)
    assert ga4_client.get_ga4_day("1999-01-01") == []


# ---------------------------------------------------------------------------
# GA4 live path
# ---------------------------------------------------------------------------


def _ga4_payload() -> dict[str, Any]:
    return {
        "rows": [
            {
                "dimensionValues": [
                    {"value": "/blog/fabric-production-proof"},
                    {"value": "fabric-production-proof"},
                ],
                "metricValues": [{"value": "120"}, {"value": "80"}, {"value": "5"}],
            },
            {
                "dimensionValues": [{"value": "/pricing"}, {"value": "(not set)"}],
                "metricValues": [{"value": "200"}, {"value": "150"}, {"value": "12"}],
            },
        ]
    }


@pytest.fixture
def ga4_live(monkeypatch):
    monkeypatch.setenv("GA4_SERVICE_ACCOUNT_KEY", "dummy")
    monkeypatch.setenv("GA4_PROPERTY_ID", "399508463")


def test_ga4_live_mode_calls_data_api_and_maps_rows(ga4_live, fake_token):
    url = f"{GA4_BASE}/properties/399508463:runReport"
    with respx.mock as mock:
        route = mock.post(url).mock(return_value=httpx.Response(200, json=_ga4_payload()))
        rows = ga4_client.get_ga4_day(FIXTURE_DAY)

    assert route.call_count == 1
    request = route.calls[0].request
    assert request.headers["Authorization"] == "Bearer fake-access-token"
    body = json.loads(request.content)
    assert body["dateRanges"] == [{"startDate": FIXTURE_DAY, "endDate": FIXTURE_DAY}]
    assert [d["name"] for d in body["dimensions"]] == [
        "landingPagePlusQueryString",
        "sessionCampaignName",
    ]

    # Exactly the row shape ingest.ingest_ga4_day already consumes.
    assert rows[0] == {
        "landing_page": "/blog/fabric-production-proof",
        "sessions": 120,
        "engaged_sessions": 80,
        "conversions": 5,
        "utm_campaign": "fabric-production-proof",
    }


def test_ga4_maps_not_set_campaign_to_none_not_a_quarantine_row(ga4_live, fake_token):
    """'(not set)' must become null, or utm.reconcile_utm quarantines a
    literal '(not set)' as an unmatched campaign every single night."""
    with respx.mock as mock:
        mock.post(f"{GA4_BASE}/properties/399508463:runReport").mock(
            return_value=httpx.Response(200, json=_ga4_payload())
        )
        rows = ga4_client.get_ga4_day(FIXTURE_DAY)
    assert rows[1]["utm_campaign"] is None


def test_ga4_retries_once_with_legacy_conversions_metric(ga4_live, fake_token):
    """A property that has not migrated to key events rejects `keyEvents`;
    the client must retry with `conversions` rather than fail the night."""
    with respx.mock as mock:
        route = mock.post(f"{GA4_BASE}/properties/399508463:runReport").mock(
            side_effect=[
                httpx.Response(400, text="Field keyEvents did not match any metric"),
                httpx.Response(200, json=_ga4_payload()),
            ]
        )
        rows = ga4_client.get_ga4_day(FIXTURE_DAY)

    assert route.call_count == 2
    first = json.loads(route.calls[0].request.content)
    second = json.loads(route.calls[1].request.content)
    assert first["metrics"][2]["name"] == "keyEvents"
    assert second["metrics"][2]["name"] == "conversions"
    assert rows[0]["conversions"] == 5


def test_ga4_does_not_retry_on_an_unrelated_400(ga4_live, fake_token):
    with respx.mock as mock:
        route = mock.post(f"{GA4_BASE}/properties/399508463:runReport").mock(
            return_value=httpx.Response(400, text="property id is invalid")
        )
        with pytest.raises(ga4_client.GA4ClientError):
            ga4_client.get_ga4_day(FIXTURE_DAY)
    assert route.call_count == 1


def test_ga4_key_without_property_id_raises_rather_than_serving_fixtures(monkeypatch):
    """The dangerous failure this guards: silently returning fixture rows
    into a rollup that believes they are real production numbers."""
    monkeypatch.setenv("GA4_SERVICE_ACCOUNT_KEY", "dummy")
    monkeypatch.delenv("GA4_PROPERTY_ID", raising=False)
    monkeypatch.delenv("KEY_VAULT_URI", raising=False)

    with pytest.raises(ga4_client.GA4ClientError, match="GA4_PROPERTY_ID"):
        ga4_client.get_ga4_day(FIXTURE_DAY)


# ---------------------------------------------------------------------------
# Search Console live path
# ---------------------------------------------------------------------------


def _gsc_payload(count: int = 2) -> dict[str, Any]:
    return {
        "rows": [
            {
                "keys": [f"query {i}", f"/page-{i}"],
                "clicks": 25 + i,
                "impressions": 400 + i,
                "ctr": 0.0625,
                "position": 8.2,
            }
            for i in range(count)
        ]
    }


@pytest.fixture
def gsc_live(monkeypatch):
    monkeypatch.setenv("SEARCH_CONSOLE_SERVICE_ACCOUNT_KEY", "dummy")
    monkeypatch.setenv("SEARCH_CONSOLE_SITE_URL", "sc-domain:canvasintelligence.com")


def test_search_console_live_mode_calls_api_and_maps_rows(gsc_live, fake_token):
    url = f"{GSC_BASE}/sites/sc-domain%3Acanvasintelligence.com/searchAnalytics/query"
    with respx.mock as mock:
        route = mock.post(url).mock(return_value=httpx.Response(200, json=_gsc_payload()))
        rows = search_console_client.get_search_console_day(FIXTURE_DAY)

    assert route.call_count == 1
    body = json.loads(route.calls[0].request.content)
    assert body["dimensions"] == ["query", "page"]
    assert body["startDate"] == FIXTURE_DAY and body["endDate"] == FIXTURE_DAY
    assert rows[0] == {
        "query": "query 0",
        "page": "/page-0",
        "clicks": 25,
        "impressions": 400,
        "ctr": 0.0625,
        "position": 8.2,
    }


def test_search_console_url_prefix_property_is_percent_encoded(monkeypatch, fake_token):
    """A URL-prefix property contains a colon and slashes; unencoded they
    would corrupt the path and return 403 rather than an obvious error."""
    monkeypatch.setenv("SEARCH_CONSOLE_SERVICE_ACCOUNT_KEY", "dummy")
    monkeypatch.setenv("SEARCH_CONSOLE_SITE_URL", "https://canvasintelligence.com/")
    url = f"{GSC_BASE}/sites/https%3A%2F%2Fcanvasintelligence.com%2F/searchAnalytics/query"
    with respx.mock as mock:
        route = mock.post(url).mock(return_value=httpx.Response(200, json=_gsc_payload()))
        search_console_client.get_search_console_day(FIXTURE_DAY)
    assert route.call_count == 1


def test_search_console_pages_until_a_short_page(gsc_live, fake_token, monkeypatch):
    monkeypatch.setattr(search_console_client, "_ROW_LIMIT", 2)
    url = f"{GSC_BASE}/sites/sc-domain%3Acanvasintelligence.com/searchAnalytics/query"
    with respx.mock as mock:
        route = mock.post(url).mock(
            side_effect=[
                httpx.Response(200, json=_gsc_payload(2)),
                httpx.Response(200, json=_gsc_payload(1)),
            ]
        )
        rows = search_console_client.get_search_console_day(FIXTURE_DAY)

    assert route.call_count == 2
    assert json.loads(route.calls[0].request.content)["startRow"] == 0
    assert json.loads(route.calls[1].request.content)["startRow"] == 2
    assert len(rows) == 3


def test_search_console_key_without_site_url_raises(monkeypatch):
    monkeypatch.setenv("SEARCH_CONSOLE_SERVICE_ACCOUNT_KEY", "dummy")
    monkeypatch.delenv("SEARCH_CONSOLE_SITE_URL", raising=False)
    monkeypatch.delenv("KEY_VAULT_URI", raising=False)

    with pytest.raises(
        search_console_client.SearchConsoleClientError, match="SEARCH_CONSOLE_SITE_URL"
    ):
        search_console_client.get_search_console_day(FIXTURE_DAY)


def test_search_console_non_200_raises(gsc_live, fake_token):
    url = f"{GSC_BASE}/sites/sc-domain%3Acanvasintelligence.com/searchAnalytics/query"
    with respx.mock as mock:
        mock.post(url).mock(return_value=httpx.Response(403, text="forbidden"))
        with pytest.raises(search_console_client.SearchConsoleClientError, match="403"):
            search_console_client.get_search_console_day(FIXTURE_DAY)


# ---------------------------------------------------------------------------
# Service-account key parsing
# ---------------------------------------------------------------------------


def _valid_key() -> dict[str, str]:
    return {
        "client_email": "cmos-analytics@canvas.iam.gserviceaccount.com",
        "private_key": "-----BEGIN PRIVATE KEY-----x-----END PRIVATE KEY-----",
        "token_uri": "https://oauth2.googleapis.com/token",
    }


def test_parse_service_account_key_accepts_raw_json():
    parsed = parse_service_account_key(json.dumps(_valid_key()))
    assert parsed["client_email"].endswith("iam.gserviceaccount.com")


def test_parse_service_account_key_accepts_base64_wrapped_json():
    wrapped = base64.b64encode(json.dumps(_valid_key()).encode()).decode()
    assert parse_service_account_key(wrapped)["token_uri"].startswith("https://")


def test_parse_service_account_key_rejects_incomplete_key():
    with pytest.raises(GoogleAuthError, match="missing required field"):
        parse_service_account_key(json.dumps({"client_email": "a@b"}))


def test_parse_service_account_key_rejects_garbage():
    with pytest.raises(GoogleAuthError):
        parse_service_account_key("not json and not base64 !!!")


def test_parse_service_account_key_rejects_empty():
    with pytest.raises(GoogleAuthError, match="empty"):
        parse_service_account_key("   ")
