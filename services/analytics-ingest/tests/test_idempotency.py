"""AC-11..AC-14 — ingesting each source's fixture day twice yields the same row count both times."""

from __future__ import annotations

from datetime import date

from analytics_ingest.ingest import (
    ingest_buffer_day,
    ingest_ga4_day,
    ingest_linkedin_day,
    ingest_search_console_day,
)
from conftest import FIXTURE_DAY, load_fixture

DAY = date.fromisoformat(FIXTURE_DAY)


async def test_buffer_fixture_day_ingest_twice_same_row_counts(pool):
    payload = load_fixture(f"buffer_{FIXTURE_DAY}.json")

    first = await ingest_buffer_day(pool, DAY, payload)
    second = await ingest_buffer_day(pool, DAY, payload)

    assert first["row_count"] == 4
    assert second["row_count"] == 4
    assert first["rows_inserted"] == 4
    assert second["rows_inserted"] == 0


async def test_ga4_fixture_day_ingest_twice_same_row_counts(pool):
    payload = load_fixture(f"ga4_{FIXTURE_DAY}.json")

    first = await ingest_ga4_day(pool, DAY, payload)
    second = await ingest_ga4_day(pool, DAY, payload)

    assert first["row_count"] == 3
    assert second["row_count"] == 3
    assert first["rows_inserted"] == 3
    assert second["rows_inserted"] == 0


async def test_search_console_fixture_day_ingest_twice_same_row_counts(pool):
    payload = load_fixture(f"search_console_{FIXTURE_DAY}.json")

    first = await ingest_search_console_day(pool, DAY, payload)
    second = await ingest_search_console_day(pool, DAY, payload)

    assert first["row_count"] == 3
    assert second["row_count"] == 3
    assert first["rows_inserted"] == 3
    assert second["rows_inserted"] == 0


async def test_linkedin_fixture_day_ingest_twice_same_row_counts(pool):
    payload = load_fixture(f"linkedin_{FIXTURE_DAY}.json")

    first = await ingest_linkedin_day(pool, DAY, payload)
    second = await ingest_linkedin_day(pool, DAY, payload)

    assert first["row_count"] == 3
    assert second["row_count"] == 3
    assert first["rows_inserted"] == 3
    assert second["rows_inserted"] == 0
