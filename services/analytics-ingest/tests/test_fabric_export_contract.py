"""AC-17 — the nightly Fabric export validates against its committed JSON Schema contract."""

from __future__ import annotations

import json
from datetime import date

import jsonschema

from analytics_ingest import vault_client
from analytics_ingest.config import contracts_dir
from analytics_ingest.fabric_export import export_fabric_day
from analytics_ingest.ingest import ingest_buffer_day, ingest_linkedin_day
from analytics_ingest.rollup import (
    rollup_cost_per_accepted_asset,
    rollup_engagement_by_archetype,
    rollup_publishing_reliability,
    rollup_vault_utilisation,
)

from conftest import FIXTURE_DAY, load_fixture

DAY = date.fromisoformat(FIXTURE_DAY)


async def test_nightly_export_matches_fabric_shortcut_schema_contract(
    pool, seeded_scheduled_posts, mock_vault_api
):
    await ingest_buffer_day(pool, DAY, load_fixture(f"buffer_{FIXTURE_DAY}.json"))
    await ingest_linkedin_day(pool, DAY, load_fixture(f"linkedin_{FIXTURE_DAY}.json"))
    await rollup_engagement_by_archetype(pool, DAY)
    await rollup_publishing_reliability(pool, DAY)
    await rollup_cost_per_accepted_asset(pool, vault_client, DAY)
    await rollup_vault_utilisation(pool, vault_client, DAY)

    # export_fabric_day self-validates internally and raises on mismatch —
    # a clean return already proves validity, but we validate again here
    # explicitly against the committed contract file for a second,
    # independent check.
    export = await export_fabric_day(pool, DAY)

    schema_path = contracts_dir() / "fabric-nightly-export.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    jsonschema.validate(export, schema)

    assert export["day"] == FIXTURE_DAY
    assert len(export["engagement_by_archetype"]) == 3
    assert len(export["publishing_reliability"]) == 2
    assert len(export["cost_per_accepted_asset"]) == 1
    assert len(export["vault_utilisation"]) == 2
