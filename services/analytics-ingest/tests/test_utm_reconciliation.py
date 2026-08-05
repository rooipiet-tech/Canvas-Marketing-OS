"""AC-15/AC-16 — UTM reconciliation (analytics.utm_campaign_map / analytics.utm_quarantine)."""

from __future__ import annotations

from datetime import date

from analytics_ingest.utm import reconcile_utm
from conftest import FIXTURE_DAY

DAY = date.fromisoformat(FIXTURE_DAY)


async def test_seeded_utm_maps_to_asset_and_campaign(seeded_utm_map):
    pool = seeded_utm_map

    result = await reconcile_utm(pool, "buffer", DAY, "buf-p1", "fabric-production-proof")

    assert result is not None
    vault_campaign_id, asset_id = result
    assert vault_campaign_id == "3fa85f64-5717-4562-b3fc-2c963f66afa6"
    assert asset_id == "7c9e6679-7425-40de-944b-e07fc1f90ae7"

    async with pool.acquire() as conn:
        quarantine_count = await conn.fetchval(
            "SELECT COUNT(*) FROM analytics.utm_quarantine WHERE natural_row_id = 'buf-p1'"
        )
    assert quarantine_count == 0


async def test_malformed_utm_quarantined_with_reason(pool):
    result = await reconcile_utm(pool, "buffer", DAY, "buf-p3", "malformed!!campaign")

    assert result is None

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT reason, utm_campaign_raw FROM analytics.utm_quarantine
            WHERE source = 'buffer' AND day = $1 AND natural_row_id = 'buf-p3'
            """,
            DAY,
        )

    assert row is not None
    assert row["reason"] is not None
    assert row["reason"] != ""
    assert row["utm_campaign_raw"] == "malformed!!campaign"


async def test_unmatched_well_formed_utm_quarantined_with_reason(pool):
    """buf-p4's utm_campaign is well-formed (matches the slug regex) but has
    no analytics.utm_campaign_map entry — quarantined with a distinct
    'unmatched' reason, not the 'invalid format' one."""
    result = await reconcile_utm(pool, "buffer", DAY, "buf-p4", "unknown-campaign-xyz")

    assert result is None

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT reason FROM analytics.utm_quarantine
            WHERE source = 'buffer' AND day = $1 AND natural_row_id = 'buf-p4'
            """,
            DAY,
        )

    assert row is not None
    assert "unmatched" in row["reason"]


async def test_missing_utm_quarantined_with_reason(pool):
    result = await reconcile_utm(pool, "ga4", DAY, "/pricing", None)

    assert result is None

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT reason FROM analytics.utm_quarantine
            WHERE source = 'ga4' AND day = $1 AND natural_row_id = '/pricing'
            """,
            DAY,
        )

    assert row is not None
    assert row["reason"] == "missing_utm_campaign"
