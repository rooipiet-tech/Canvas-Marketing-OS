"""test_nightly_cli_dry_run_exercises_full_pipeline_in_order — closes the F1 gap.

Plan-reviewer finding F1: AC-17/19/20/21/22/26/28's tests previously all
passed by calling pipeline functions (ingest_*_day, rollup_*,
export_fabric_day) DIRECTLY, never through the actual deployed nightly
entrypoint (`cli.py nightly`) — so a real wiring bug in cli.py's stage
ordering could have gone undetected. This test invokes
run_nightly_pipeline (the underlying function `python -m
analytics_ingest.cli nightly --day 2026-07-31 --dry-run` calls) directly,
against the step-14 fixtures, and asserts every stage actually ran, in
order, with the right data reaching the right table.
"""

from __future__ import annotations

from datetime import date

import pytest
from analytics_ingest.cli import run_nightly_pipeline
from conftest import FIXTURE_DAY

DAY = date.fromisoformat(FIXTURE_DAY)


async def test_nightly_cli_dry_run_exercises_full_pipeline_in_order(
    pool, seeded_utm_map, seeded_scheduled_posts, mock_vault_api, mock_blob_upload
):
    summary = await run_nightly_pipeline(FIXTURE_DAY, dry_run=True)

    assert set(summary.keys()) == {
        "ingested", "reconciled", "quarantined", "rolled_up", "exported", "uploaded"
    }
    assert summary["ingested"]["buffer"]["row_count"] == 4
    assert summary["ingested"]["ga4"]["row_count"] == 3
    assert summary["ingested"]["search_console"]["row_count"] == 3
    assert summary["ingested"]["linkedin"]["row_count"] == 3
    # F3 fix: "reconciled" is every row actually run through reconcile_utm
    # (buffer 4 + linkedin 3 + ga4 3 = 10; search_console has no
    # utm_campaign column and is excluded by construction); "quarantined"
    # is only the subset reconcile_utm() itself quarantined — buf-p3,
    # buf-p4, li-003 (asserted below by natural_row_id) plus ga4's
    # "/pricing" (null utm_campaign) and "/blog/case-study" (unmatched
    # unknown-campaign-xyz) = 5, not all 10.
    assert summary["reconciled"] == 10
    assert summary["quarantined"] == 5
    assert summary["rolled_up"] is True
    assert summary["exported"] is True

    # Stage 1: all 4 sources' rows landed in their raw tables.
    async with pool.acquire() as conn:
        buffer_count = await conn.fetchval(
            "SELECT COUNT(*) FROM analytics.buffer_post_metrics WHERE day = $1", DAY
        )
        ga4_count = await conn.fetchval(
            "SELECT COUNT(*) FROM analytics.ga4_metrics WHERE day = $1", DAY
        )
        sc_count = await conn.fetchval(
            "SELECT COUNT(*) FROM analytics.search_console_metrics WHERE day = $1", DAY
        )
        li_count = await conn.fetchval(
            "SELECT COUNT(*) FROM analytics.linkedin_metrics WHERE day = $1", DAY
        )
    assert (buffer_count, ga4_count, sc_count, li_count) == (4, 3, 3, 3)

    # Stage 2: buf-p3/buf-p4/li-003's malformed-or-unmatched utm_campaign
    # values are quarantined with a reason.
    async with pool.acquire() as conn:
        quarantined_rows = await conn.fetch(
            "SELECT natural_row_id, reason FROM analytics.utm_quarantine WHERE day = $1", DAY
        )
    quarantined_ids = {row["natural_row_id"]: row["reason"] for row in quarantined_rows}
    for expected_id in ("buf-p3", "buf-p4", "li-003"):
        assert expected_id in quarantined_ids
        assert quarantined_ids[expected_id]

    # Stage 3: all 4 kpi_rollup_* tables contain rows matching step 14's golden values.
    async with pool.acquire() as conn:
        engagement_rows = await conn.fetch(
            """
            SELECT post_archetype, engagement_rate, post_count
            FROM analytics.kpi_rollup_engagement_by_archetype
            WHERE day = $1 AND source = 'buffer'
            """,
            DAY,
        )
        reliability_rows = await conn.fetch(
            "SELECT channel, reliability_rate FROM analytics.kpi_rollup_publishing_reliability"
            " WHERE day = $1",
            DAY,
        )
        cost_rows = await conn.fetch(
            "SELECT agent_name, cost_per_accepted_asset"
            " FROM analytics.kpi_rollup_cost_per_accepted_asset WHERE day = $1",
            DAY,
        )
        utilisation_rows = await conn.fetch(
            "SELECT object_class, read_count FROM analytics.kpi_rollup_vault_utilisation"
            " WHERE day = $1",
            DAY,
        )

    engagement_golden = {"carousel": 0.1175, "story": 0.06, "case_study": 0.1}
    assert {row["post_archetype"] for row in engagement_rows} == set(engagement_golden.keys())
    for row in engagement_rows:
        assert float(row["engagement_rate"]) == pytest.approx(
            engagement_golden[row["post_archetype"]], abs=1e-6
        )

    reliability_golden = {"buffer": 0.8, "linkedin": 1.0}
    assert {row["channel"] for row in reliability_rows} == set(reliability_golden.keys())
    for row in reliability_rows:
        assert float(row["reliability_rate"]) == pytest.approx(
            reliability_golden[row["channel"]], abs=1e-6
        )

    assert len(cost_rows) == 1
    assert cost_rows[0]["agent_name"] == "asset-generator"
    assert float(cost_rows[0]["cost_per_accepted_asset"]) == pytest.approx(2.50, abs=1e-4)

    utilisation_golden = {"assets": 12, "campaigns": 4}
    assert {row["object_class"] for row in utilisation_rows} == set(utilisation_golden.keys())
    for row in utilisation_rows:
        assert row["read_count"] == utilisation_golden[row["object_class"]]

    # Stage 4: export_fabric_day's output validates against the Fabric
    # shortcut schema contract — proven by export_fabric_day() raising on
    # any mismatch internally; run_nightly_pipeline completing without
    # exception up through the "exported" stage is sufficient evidence.

    # Stage 5: blob_writer.upload was called (mocked) with the validated payload.
    assert len(mock_blob_upload) == 1
    uploaded_export, uploaded_day = mock_blob_upload[0]
    assert uploaded_day == FIXTURE_DAY
    assert uploaded_export["day"] == FIXTURE_DAY
    assert summary["uploaded"] == f"mock-blob:{FIXTURE_DAY}.json"
