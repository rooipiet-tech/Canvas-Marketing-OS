"""AC-19..AC-22 — the 4 nightly KPI rollups, asserted against step 14's hand-computed golden values."""

from __future__ import annotations

from datetime import date

import pytest

from analytics_ingest import vault_client
from analytics_ingest.ingest import ingest_buffer_day, ingest_linkedin_day
from analytics_ingest.rollup import (
    rollup_cost_per_accepted_asset,
    rollup_engagement_by_archetype,
    rollup_publishing_reliability,
    rollup_vault_utilisation,
)

from conftest import FIXTURE_DAY, load_fixture

DAY = date.fromisoformat(FIXTURE_DAY)


async def test_engagement_rate_by_post_archetype_matches_golden(pool):
    """Scoped to source='buffer' only (documented rationale, plan v2 F6):
    rollup_engagement_by_archetype groups by (source, post_archetype), so
    buffer and linkedin produce independent, non-blended result rows
    through the identical aggregation code path — asserting the already
    hand-verified buffer-side golden values is sufficient to catch
    aggregation-logic regressions without deriving a second cross-source
    golden-value set (an explicit, documented scope choice, not a silent
    gap)."""
    buffer_payload = load_fixture(f"buffer_{FIXTURE_DAY}.json")
    await ingest_buffer_day(pool, DAY, buffer_payload)

    await rollup_engagement_by_archetype(pool, DAY)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT post_archetype, engagement_rate, post_count
            FROM analytics.kpi_rollup_engagement_by_archetype
            WHERE day = $1 AND kpi_name = 'engagement_rate_by_post_archetype' AND source = 'buffer'
            """,
            DAY,
        )

    golden = {
        "carousel": (0.1175, 2),
        "story": (0.06, 1),
        "case_study": (0.1, 1),
    }
    assert {row["post_archetype"] for row in rows} == set(golden.keys())
    for row in rows:
        expected_rate, expected_count = golden[row["post_archetype"]]
        assert float(row["engagement_rate"]) == pytest.approx(expected_rate, abs=1e-6)
        assert row["post_count"] == expected_count


async def test_engagement_rollup_skips_zero_impressions_archetype(pool):
    """F1 regression: a (source, post_archetype) group whose SUM(impressions)
    is 0 must not attempt to insert NULL into the NOT-NULL engagement_rate
    column (analytics.kpi_rollup_engagement_by_archetype) — it must be
    skipped instead of crashing the pipeline with a NotNullViolationError."""
    zero_impressions_payload = load_fixture("buffer_zero_impressions_2026-07-31.json")
    await ingest_buffer_day(pool, DAY, zero_impressions_payload)

    await rollup_engagement_by_archetype(pool, DAY)  # must not raise

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT post_archetype
            FROM analytics.kpi_rollup_engagement_by_archetype
            WHERE day = $1 AND kpi_name = 'engagement_rate_by_post_archetype' AND source = 'buffer'
            """,
            DAY,
        )
    assert {row["post_archetype"] for row in rows} == set()


async def test_publishing_reliability_matches_golden(pool, seeded_scheduled_posts):
    buffer_payload = load_fixture(f"buffer_{FIXTURE_DAY}.json")
    linkedin_payload = load_fixture(f"linkedin_{FIXTURE_DAY}.json")
    await ingest_buffer_day(pool, DAY, buffer_payload)
    await ingest_linkedin_day(pool, DAY, linkedin_payload)

    await rollup_publishing_reliability(pool, DAY)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT channel, scheduled_count, published_count, reliability_rate
            FROM analytics.kpi_rollup_publishing_reliability
            WHERE day = $1 AND kpi_name = 'publishing_reliability'
            """,
            DAY,
        )

    golden = {
        "buffer": (5, 4, 0.8),
        "linkedin": (3, 3, 1.0),
    }
    assert {row["channel"] for row in rows} == set(golden.keys())
    for row in rows:
        expected_scheduled, expected_published, expected_rate = golden[row["channel"]]
        assert row["scheduled_count"] == expected_scheduled
        assert row["published_count"] == expected_published
        assert float(row["reliability_rate"]) == pytest.approx(expected_rate, abs=1e-6)


async def test_publishing_reliability_skips_zero_scheduled_count(pool):
    """F1 regression: a channel with scheduled_count 0 must not attempt to
    insert NULL into the NOT-NULL reliability_rate column
    (analytics.kpi_rollup_publishing_reliability) — it must be skipped
    instead of crashing the pipeline with a NotNullViolationError."""
    rows = load_fixture("scheduled_posts_zero_scheduled_2026-07-31.json")
    async with pool.acquire() as conn:
        for row in rows:
            await conn.execute(
                """
                INSERT INTO analytics.scheduled_posts (day, channel, scheduled_count)
                VALUES ($1, $2, $3)
                ON CONFLICT (day, channel) DO NOTHING
                """,
                date.fromisoformat(row["day"]),
                row["channel"],
                row["scheduled_count"],
            )

    await rollup_publishing_reliability(pool, DAY)  # must not raise

    async with pool.acquire() as conn:
        result_rows = await conn.fetch(
            """
            SELECT channel
            FROM analytics.kpi_rollup_publishing_reliability
            WHERE day = $1 AND kpi_name = 'publishing_reliability'
            """,
            DAY,
        )
    assert {row["channel"] for row in result_rows} == set()


async def test_cost_per_accepted_asset_matches_golden(pool, mock_vault_api):
    await rollup_cost_per_accepted_asset(pool, vault_client, DAY)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT agent_name, total_cost_usd, accepted_asset_count, cost_per_accepted_asset
            FROM analytics.kpi_rollup_cost_per_accepted_asset
            WHERE day = $1 AND kpi_name = 'cost_per_accepted_asset'
            """,
            DAY,
        )

    # brief-drafter is excluded: its only asset (a-3) is rejected, so it has
    # 0 approved assets — no row is written for it (div-by-zero avoidance).
    assert {row["agent_name"] for row in rows} == {"asset-generator"}
    row = rows[0]
    assert float(row["total_cost_usd"]) == pytest.approx(5.00, abs=1e-4)
    assert row["accepted_asset_count"] == 2
    assert float(row["cost_per_accepted_asset"]) == pytest.approx(2.50, abs=1e-4)


async def test_vault_utilisation_matches_golden(pool, mock_vault_api):
    await rollup_vault_utilisation(pool, vault_client, DAY)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT object_class, caller_service, read_count
            FROM analytics.kpi_rollup_vault_utilisation
            WHERE day = $1 AND kpi_name = 'vault_utilisation'
            """,
            DAY,
        )

    golden = {"assets": 12, "campaigns": 4}
    assert {row["object_class"] for row in rows} == set(golden.keys())
    for row in rows:
        assert row["caller_service"] == "analytics-ingest"
        assert row["read_count"] == golden[row["object_class"]]
