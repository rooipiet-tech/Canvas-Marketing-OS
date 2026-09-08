"""LinkedIn's live path — the same shape of guarantee test_google_live_clients.py
gives GA4/Search Console: fixture mode is untouched when no secret is
present (AC-27), and live mode issues the right requests and maps the
response onto the row shape ingest.ingest_linkedin_day already consumes.

The OAuth token exchange is monkeypatched (`linkedin_client._access_token`)
for every test except the small block that exercises it directly, mirroring
test_google_live_clients.py's `fake_token` fixture pattern — the two
concerns (token minting, and what the client does with a token) are tested
separately.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx
from analytics_ingest import linkedin_client

FIXTURE_DAY = "2026-07-31"
ORG_URN = "urn:li:organization:12345678"
_DAY_START_MS, _ = linkedin_client._day_bounds_ms(FIXTURE_DAY)
POSTS_URL = f"{linkedin_client.LINKEDIN_API_BASE}/posts"
STATS_URL = f"{linkedin_client.LINKEDIN_API_BASE}/organizationalEntityShareStatistics"
OAUTH_URL = linkedin_client.LINKEDIN_OAUTH_TOKEN_URL


@pytest.fixture
def fake_token(monkeypatch):
    monkeypatch.setattr(linkedin_client, "_access_token", lambda _client: "fake-access-token")


@pytest.fixture
def linkedin_live(monkeypatch):
    monkeypatch.setenv("LINKEDIN_ANALYTICS_CLIENT_SECRET", "dummy-secret")
    monkeypatch.setenv("LINKEDIN_ANALYTICS_CLIENT_ID", "dummy-client-id")
    monkeypatch.setenv("LINKEDIN_ANALYTICS_REFRESH_TOKEN", "dummy-refresh-token")
    monkeypatch.setenv("LINKEDIN_ANALYTICS_ORG_URN", ORG_URN)


# ---------------------------------------------------------------------------
# Fixture mode is untouched — the regression guard that matters most
# ---------------------------------------------------------------------------


def test_fixture_mode_unchanged_when_no_secret(monkeypatch):
    monkeypatch.delenv("LINKEDIN_ANALYTICS_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("KEY_VAULT_URI", raising=False)
    rows = linkedin_client.get_linkedin_day(FIXTURE_DAY)
    assert len(rows) == 3
    assert rows[0]["post_id"] == "li-001"
    assert rows[0]["post_archetype"] == "carousel"


def test_missing_fixture_still_returns_empty_list(monkeypatch):
    monkeypatch.delenv("LINKEDIN_ANALYTICS_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("KEY_VAULT_URI", raising=False)
    assert linkedin_client.get_linkedin_day("1999-01-01") == []


# ---------------------------------------------------------------------------
# Live path
# ---------------------------------------------------------------------------


def _posts_payload(*, in_window: int = 2, out_of_window: int = 0) -> dict[str, Any]:
    elements = []
    for i in range(in_window):
        elements.append(
            {
                "id": f"urn:li:share:{i}",
                "createdAt": _DAY_START_MS + i * 1000,  # FIXTURE_DAY 00:00:00Z + i seconds
                "commentary": (
                    f"Check it out https://canvasintelligence.com/blog/post-{i}"
                    f"?utm_campaign=fabric-production-proof&utm_content=carousel"
                ),
            }
        )
    for i in range(out_of_window):
        elements.append(
            {
                "id": f"urn:li:share:out-{i}",
                "createdAt": _DAY_START_MS - 86_400_000,  # the day before
                "commentary": "https://canvasintelligence.com/blog/older-post",
            }
        )
    return {"elements": elements}


def _stats_payload(count: int) -> dict[str, Any]:
    return {
        "elements": [
            {
                "totalShareStatistics": {
                    "impressionCount": 100 + i,
                    "clickCount": 10 + i,
                    "likeCount": 20 + i,
                    "commentCount": 2 + i,
                    "shareCount": 1 + i,
                }
            }
            for i in range(count)
        ]
    }


def test_live_mode_calls_posts_and_stats_apis_and_maps_rows(linkedin_live, fake_token):
    with respx.mock as mock:
        mock.get(POSTS_URL).mock(return_value=httpx.Response(200, json=_posts_payload(in_window=2)))
        stats_route = mock.get(STATS_URL).mock(
            return_value=httpx.Response(200, json=_stats_payload(2))
        )
        rows = linkedin_client.get_linkedin_day(FIXTURE_DAY)

    assert len(rows) == 2
    assert rows[0] == {
        "post_id": "urn:li:share:0",
        "post_archetype": "carousel",
        "impressions": 100,
        "clicks": 10,
        "reactions": 20,
        "comments": 2,
        "shares": 1,
        "utm_campaign": "fabric-production-proof",
    }

    stats_request = stats_route.calls[0].request
    assert stats_request.headers["Authorization"] == "Bearer fake-access-token"
    assert stats_request.headers["LinkedIn-Version"] == linkedin_client.linkedin_api_version()
    query = str(stats_request.url)
    assert "organizationalEntity=urn%3Ali%3Aorganization%3A12345678" in query
    assert "shares%5B0%5D=urn%3Ali%3Ashare%3A0" in query
    assert "shares%5B1%5D=urn%3Ali%3Ashare%3A1" in query


def test_posts_outside_day_window_are_excluded(linkedin_live, fake_token):
    with respx.mock as mock:
        mock.get(POSTS_URL).mock(
            return_value=httpx.Response(200, json=_posts_payload(in_window=1, out_of_window=1))
        )
        mock.get(STATS_URL).mock(return_value=httpx.Response(200, json=_stats_payload(1)))
        rows = linkedin_client.get_linkedin_day(FIXTURE_DAY)

    assert len(rows) == 1
    assert rows[0]["post_id"] == "urn:li:share:0"


def test_no_posts_that_day_skips_the_stats_call_entirely(linkedin_live, fake_token):
    with respx.mock as mock:
        mock.get(POSTS_URL).mock(return_value=httpx.Response(200, json={"elements": []}))
        stats_route = mock.get(STATS_URL).mock(
            return_value=httpx.Response(200, json={"elements": []})
        )
        rows = linkedin_client.get_linkedin_day(FIXTURE_DAY)

    assert rows == []
    assert stats_route.call_count == 0


def test_paginates_posts_until_a_short_page(linkedin_live, fake_token, monkeypatch):
    monkeypatch.setattr(linkedin_client, "_POSTS_PAGE_SIZE", 2)
    with respx.mock as mock:
        route = mock.get(POSTS_URL).mock(
            side_effect=[
                httpx.Response(200, json=_posts_payload(in_window=2)),
                httpx.Response(200, json=_posts_payload(in_window=1)),
            ]
        )
        mock.get(STATS_URL).mock(return_value=httpx.Response(200, json=_stats_payload(3)))
        rows = linkedin_client.get_linkedin_day(FIXTURE_DAY)

    assert route.call_count == 2
    assert route.calls[0].request.url.params["start"] == "0"
    assert route.calls[1].request.url.params["start"] == "2"
    assert len(rows) == 3


def test_batches_share_statistics_requests_past_the_cap(linkedin_live, fake_token, monkeypatch):
    monkeypatch.setattr(linkedin_client, "_MAX_SHARES_PER_STATS_CALL", 20)
    with respx.mock as mock:
        mock.get(POSTS_URL).mock(
            return_value=httpx.Response(200, json=_posts_payload(in_window=25))
        )
        stats_route = mock.get(STATS_URL).mock(
            side_effect=[
                httpx.Response(200, json=_stats_payload(20)),
                httpx.Response(200, json=_stats_payload(5)),
            ]
        )
        rows = linkedin_client.get_linkedin_day(FIXTURE_DAY)

    assert stats_route.call_count == 2
    assert len(rows) == 25


def test_posts_listing_non_200_raises(linkedin_live, fake_token):
    with respx.mock as mock:
        mock.get(POSTS_URL).mock(return_value=httpx.Response(403, text="forbidden"))
        with pytest.raises(linkedin_client.LinkedInClientError, match="403"):
            linkedin_client.get_linkedin_day(FIXTURE_DAY)


def test_stats_call_non_200_raises(linkedin_live, fake_token):
    with respx.mock as mock:
        mock.get(POSTS_URL).mock(return_value=httpx.Response(200, json=_posts_payload(in_window=1)))
        mock.get(STATS_URL).mock(return_value=httpx.Response(500, text="server error"))
        with pytest.raises(linkedin_client.LinkedInClientError, match="500"):
            linkedin_client.get_linkedin_day(FIXTURE_DAY)


def test_live_key_without_org_urn_raises(monkeypatch):
    monkeypatch.setenv("LINKEDIN_ANALYTICS_CLIENT_SECRET", "dummy-secret")
    monkeypatch.delenv("LINKEDIN_ANALYTICS_ORG_URN", raising=False)
    monkeypatch.delenv("KEY_VAULT_URI", raising=False)
    with pytest.raises(linkedin_client.LinkedInClientError, match="LINKEDIN_ANALYTICS_ORG_URN"):
        linkedin_client.get_linkedin_day(FIXTURE_DAY)


def test_live_key_without_client_id_or_refresh_token_raises(monkeypatch):
    monkeypatch.setenv("LINKEDIN_ANALYTICS_CLIENT_SECRET", "dummy-secret")
    monkeypatch.setenv("LINKEDIN_ANALYTICS_ORG_URN", ORG_URN)
    monkeypatch.delenv("LINKEDIN_ANALYTICS_CLIENT_ID", raising=False)
    monkeypatch.delenv("LINKEDIN_ANALYTICS_REFRESH_TOKEN", raising=False)
    monkeypatch.delenv("KEY_VAULT_URI", raising=False)
    with pytest.raises(
        linkedin_client.LinkedInClientError, match="LINKEDIN_ANALYTICS_CLIENT_ID"
    ):
        linkedin_client.get_linkedin_day(FIXTURE_DAY)


def test_missing_commentary_url_yields_null_attribution(linkedin_live, fake_token):
    with respx.mock as mock:
        mock.get(POSTS_URL).mock(
            return_value=httpx.Response(
                200,
                json={"elements": [{"id": "urn:li:share:x", "createdAt": _DAY_START_MS}]},
            )
        )
        mock.get(STATS_URL).mock(return_value=httpx.Response(200, json=_stats_payload(1)))
        rows = linkedin_client.get_linkedin_day(FIXTURE_DAY)

    assert rows[0]["post_archetype"] is None
    assert rows[0]["utm_campaign"] is None


# ---------------------------------------------------------------------------
# OAuth token exchange (the part fake_token bypasses everywhere else)
# ---------------------------------------------------------------------------


def test_access_token_posts_refresh_grant_and_returns_token(linkedin_live):
    with respx.mock as mock:
        route = mock.post(OAUTH_URL).mock(
            return_value=httpx.Response(
                200, json={"access_token": "real-token", "expires_in": 3600}
            )
        )
        with httpx.Client() as client:
            token = linkedin_client._access_token(client)

    assert token == "real-token"
    sent = dict(x.split("=") for x in route.calls[0].request.content.decode().split("&"))
    assert sent["grant_type"] == "refresh_token"
    assert sent["refresh_token"] == "dummy-refresh-token"
    assert sent["client_id"] == "dummy-client-id"


def test_access_token_non_200_raises(linkedin_live):
    with respx.mock as mock:
        mock.post(OAUTH_URL).mock(return_value=httpx.Response(401, text="invalid_grant"))
        with httpx.Client() as client:
            with pytest.raises(linkedin_client.LinkedInClientError, match="401"):
                linkedin_client._access_token(client)


def test_access_token_missing_access_token_field_raises(linkedin_live):
    with respx.mock as mock:
        mock.post(OAUTH_URL).mock(return_value=httpx.Response(200, json={}))
        with httpx.Client() as client:
            with pytest.raises(linkedin_client.LinkedInClientError, match="no access_token"):
                linkedin_client._access_token(client)


# ---------------------------------------------------------------------------
# _extract_utm unit coverage
# ---------------------------------------------------------------------------


def test_extract_utm_reads_both_params():
    campaign, content = linkedin_client._extract_utm(
        "See https://example.com/x?utm_campaign=abc&utm_content=story more text"
    )
    assert campaign == "abc"
    assert content == "story"


def test_extract_utm_returns_none_for_no_url():
    assert linkedin_client._extract_utm("no link here") == (None, None)


def test_extract_utm_returns_none_for_none_text():
    assert linkedin_client._extract_utm(None) == (None, None)
