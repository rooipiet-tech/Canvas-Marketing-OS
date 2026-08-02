"""Shared pytest fixtures for analytics-ingest's test suite.

DATABASE_URL-driven Postgres fixture: applies migrations/0001_analytics_init.sql
once per session (twice, back to back, to prove idempotency — mirrors
ci.yml's vault-internal-migration-test job's own idempotency check), then
truncates every analytics.* table before each test so tests never see
another test's rows.

respx-based HTTP mocks stand in for the Vault REST API (vault_client.py) —
no real ca-vault instance is reachable from this test suite; every GET the
client issues is intercepted and answered from the same seeded fixture
JSON files rollup.py's golden-value tests assert against (step 14).
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
import respx

# Re-exported so pytest discovers it as a conftest.py-scoped fixture — see
# _pg_pool.py's own header comment for why the real Postgres pool creation
# lives in a separate, Vault-reference-free file.
from _pg_pool import _migrated_pool  # noqa: F401

FIXTURES_DIR = Path(__file__).parent / "fixtures"

FIXTURE_DAY = "2026-07-31"

VAULT_API_URL = "http://vault.internal.test"

os.environ.setdefault("DATABASE_URL", "postgresql://cmos:cmos_ci_password@localhost:5432/cmos")
os.environ.setdefault("VAULT_API_URL", VAULT_API_URL)
# Deliberately never set BUFFER_API_KEY / GA4_SERVICE_ACCOUNT_KEY /
# SEARCH_CONSOLE_SERVICE_ACCOUNT_KEY / LINKEDIN_ANALYTICS_CLIENT_SECRET /
# KEY_VAULT_URI here — the whole suite runs in fixture mode by default
# (AC-27); test_dualmode_gate.py sets/unsets these itself per-test.

_ANALYTICS_TABLES = [
    "analytics.buffer_post_metrics",
    "analytics.ga4_metrics",
    "analytics.search_console_metrics",
    "analytics.linkedin_metrics",
    "analytics.utm_campaign_map",
    "analytics.utm_quarantine",
    "analytics.scheduled_posts",
    "analytics.kpi_rollup_engagement_by_archetype",
    "analytics.kpi_rollup_publishing_reliability",
    "analytics.kpi_rollup_cost_per_accepted_asset",
    "analytics.kpi_rollup_vault_utilisation",
]


def load_fixture(name: str) -> Any:
    """Load a JSON fixture file by filename (relative to tests/fixtures/)."""
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


@pytest_asyncio.fixture
async def pool(_migrated_pool):
    """Function-scoped: truncates every analytics.* table before each test."""
    async with _migrated_pool.acquire() as conn:
        await conn.execute("TRUNCATE TABLE " + ", ".join(_ANALYTICS_TABLES))
    yield _migrated_pool


@pytest_asyncio.fixture
async def seeded_utm_map(pool):
    """Seeds analytics.utm_campaign_map from utm_campaign_map_seed.json."""
    rows = load_fixture("utm_campaign_map_seed.json")
    async with pool.acquire() as conn:
        for row in rows:
            await conn.execute(
                """
                INSERT INTO analytics.utm_campaign_map
                    (utm_campaign_slug, vault_campaign_id, asset_id)
                VALUES ($1, $2, $3)
                ON CONFLICT (utm_campaign_slug) DO NOTHING
                """,
                row["utm_campaign_slug"],
                row["vault_campaign_id"],
                row["asset_id"],
            )
    return pool


@pytest_asyncio.fixture
async def seeded_scheduled_posts(pool):
    """Seeds analytics.scheduled_posts from scheduled_posts_<day>.json."""
    rows = load_fixture(f"scheduled_posts_{FIXTURE_DAY}.json")
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
    return pool


@pytest.fixture
def mock_vault_api():
    """Intercepts every httpx call vault_client.py makes and answers from the seeded fixtures."""
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{VAULT_API_URL}/costs").mock(
            return_value=httpx.Response(200, json=load_fixture(f"vault_costs_{FIXTURE_DAY}.json"))
        )
        mock.get(f"{VAULT_API_URL}/agent-runs").mock(
            return_value=httpx.Response(200, json=load_fixture(f"vault_agent_runs_{FIXTURE_DAY}.json"))
        )
        mock.get(f"{VAULT_API_URL}/assets").mock(
            return_value=httpx.Response(200, json=load_fixture(f"vault_assets_{FIXTURE_DAY}.json"))
        )
        mock.get(f"{VAULT_API_URL}/campaigns").mock(return_value=httpx.Response(200, json=[]))
        mock.get(f"{VAULT_API_URL}/utilisation/rollup").mock(
            return_value=httpx.Response(
                200, json=load_fixture(f"vault_utilisation_rollup_{FIXTURE_DAY}.json")
            )
        )
        yield mock


@pytest.fixture
def mock_blob_upload(monkeypatch):
    """Monkeypatches analytics_ingest.blob_writer.upload with a fast, network-free stub.

    Records every call as (export_json, day) so tests can assert
    blob_writer.upload was actually invoked with the validated payload.
    """
    calls: list[tuple[dict, str]] = []

    async def _fake_upload(export_json: dict, day: str) -> str:
        calls.append((export_json, day))
        return f"mock-blob:{day}.json"

    monkeypatch.setattr("analytics_ingest.blob_writer.upload", _fake_upload)
    return calls
