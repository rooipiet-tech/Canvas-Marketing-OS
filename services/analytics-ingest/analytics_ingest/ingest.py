"""analytics_ingest.ingest — idempotent raw-fact-table ingestion.

Each ingest_*_day function parses a source's row list and executes
INSERT INTO analytics.<table> (...) VALUES (...) ON CONFLICT (source, day,
natural_row_id) DO NOTHING per row — never clobbers a hand-corrected
quarantine row or a previously-ingested row (C-IDEMPOTENCY / AC-08).

rows_inserted is computed as an explicit before/after diff: SELECT
COUNT(*) FROM analytics.<table> WHERE day=$1 taken immediately before the
INSERT block and again immediately after, with
rows_inserted = after_count - before_count — never derived from asyncpg's
executemany() return value, which carries no per-row command tags and
cannot distinguish inserted rows from ON CONFLICT DO NOTHING no-ops.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import asyncpg


async def _count_for_day(pool: asyncpg.Pool, table: str, day: date) -> int:
    async with pool.acquire() as conn:
        result = await conn.fetchval(
            f"SELECT COUNT(*) FROM analytics.{table} WHERE day = $1", day
        )
        return int(result)


async def ingest_buffer_day(
    pool: asyncpg.Pool, day: date, payload: list[dict[str, Any]]
) -> dict[str, int]:
    """Idempotently ingest Buffer post-metrics rows for `day`."""
    table = "buffer_post_metrics"
    before_count = await _count_for_day(pool, table, day)

    async with pool.acquire() as conn:
        for row in payload:
            await conn.execute(
                """
                INSERT INTO analytics.buffer_post_metrics
                    (source, day, natural_row_id, post_id, utm_campaign,
                     impressions, reactions, comments, shares, clicks,
                     engagement_rate, post_archetype)
                VALUES ('buffer', $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                ON CONFLICT (source, day, natural_row_id) DO NOTHING
                """,
                day,
                row["post_id"],
                row["post_id"],
                row.get("utm_campaign"),
                row.get("impressions"),
                row.get("reactions"),
                row.get("comments"),
                row.get("shares"),
                row.get("clicks"),
                _engagement_rate(row),
                row.get("post_archetype"),
            )

    after_count = await _count_for_day(pool, table, day)
    return {"rows_inserted": after_count - before_count, "row_count": after_count}


async def ingest_ga4_day(
    pool: asyncpg.Pool, day: date, payload: list[dict[str, Any]]
) -> dict[str, int]:
    """Idempotently ingest GA4 rows for `day`."""
    table = "ga4_metrics"
    before_count = await _count_for_day(pool, table, day)

    async with pool.acquire() as conn:
        for row in payload:
            natural_row_id = row.get("landing_page", "")
            await conn.execute(
                """
                INSERT INTO analytics.ga4_metrics
                    (source, day, natural_row_id, landing_page, sessions,
                     engaged_sessions, conversions, utm_campaign)
                VALUES ('ga4', $1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (source, day, natural_row_id) DO NOTHING
                """,
                day,
                natural_row_id,
                row.get("landing_page"),
                row.get("sessions"),
                row.get("engaged_sessions"),
                row.get("conversions"),
                row.get("utm_campaign"),
            )

    after_count = await _count_for_day(pool, table, day)
    return {"rows_inserted": after_count - before_count, "row_count": after_count}


async def ingest_search_console_day(
    pool: asyncpg.Pool, day: date, payload: list[dict[str, Any]]
) -> dict[str, int]:
    """Idempotently ingest Search Console rows for `day`."""
    table = "search_console_metrics"
    before_count = await _count_for_day(pool, table, day)

    async with pool.acquire() as conn:
        for row in payload:
            natural_row_id = f"{row.get('page', '')}::{row.get('query', '')}"
            await conn.execute(
                """
                INSERT INTO analytics.search_console_metrics
                    (source, day, natural_row_id, query, page, clicks,
                     impressions, ctr, position)
                VALUES ('search_console', $1, $2, $3, $4, $5, $6, $7, $8)
                ON CONFLICT (source, day, natural_row_id) DO NOTHING
                """,
                day,
                natural_row_id,
                row.get("query"),
                row.get("page"),
                row.get("clicks"),
                row.get("impressions"),
                row.get("ctr"),
                row.get("position"),
            )

    after_count = await _count_for_day(pool, table, day)
    return {"rows_inserted": after_count - before_count, "row_count": after_count}


async def ingest_linkedin_day(
    pool: asyncpg.Pool, day: date, payload: list[dict[str, Any]]
) -> dict[str, int]:
    """Idempotently ingest LinkedIn post-metrics rows for `day`."""
    table = "linkedin_metrics"
    before_count = await _count_for_day(pool, table, day)

    async with pool.acquire() as conn:
        for row in payload:
            await conn.execute(
                """
                INSERT INTO analytics.linkedin_metrics
                    (source, day, natural_row_id, post_id, utm_campaign,
                     impressions, reactions, comments, shares, clicks,
                     engagement_rate, post_archetype)
                VALUES ('linkedin', $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                ON CONFLICT (source, day, natural_row_id) DO NOTHING
                """,
                day,
                row["post_id"],
                row["post_id"],
                row.get("utm_campaign"),
                row.get("impressions"),
                row.get("reactions"),
                row.get("comments"),
                row.get("shares"),
                row.get("clicks"),
                _engagement_rate(row),
                row.get("post_archetype"),
            )

    after_count = await _count_for_day(pool, table, day)
    return {"rows_inserted": after_count - before_count, "row_count": after_count}


def _engagement_rate(row: dict[str, Any]) -> float | None:
    """(reactions + comments + shares + clicks) / impressions, or None."""
    impressions = row.get("impressions")
    if not impressions:
        return None
    numerator = (
        (row.get("reactions") or 0)
        + (row.get("comments") or 0)
        + (row.get("shares") or 0)
        + (row.get("clicks") or 0)
    )
    return numerator / impressions
