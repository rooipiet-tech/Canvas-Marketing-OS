"""analytics_ingest.fabric_export — nightly Fabric shortcut export.

export_fabric_day() queries the 4 KPI rollup tables, assembles the export
dict in the exact shape analytics/contracts/fabric-nightly-export.schema.json
describes, and self-validates it via jsonschema.validate before returning
it (AC-17) — never uploads/returns a payload that hasn't been checked
against the committed contract.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

import jsonschema

from analytics_ingest.config import contracts_dir


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _row_to_dict(row: Any) -> dict[str, Any]:
    return {key: _jsonable(value) for key, value in dict(row).items()}


async def export_fabric_day(pool: Any, day: date) -> dict[str, Any]:
    """Assemble and self-validate the nightly Fabric export for `day`."""
    async with pool.acquire() as conn:
        engagement_rows = await conn.fetch(
            """
            SELECT day, kpi_name, source, post_archetype, engagement_rate, post_count
            FROM analytics.kpi_rollup_engagement_by_archetype
            WHERE day = $1
            ORDER BY source, post_archetype
            """,
            day,
        )
        reliability_rows = await conn.fetch(
            """
            SELECT day, kpi_name, channel, scheduled_count, published_count, reliability_rate
            FROM analytics.kpi_rollup_publishing_reliability
            WHERE day = $1
            ORDER BY channel
            """,
            day,
        )
        cost_rows = await conn.fetch(
            """
            SELECT day, kpi_name, agent_name, total_cost_usd, accepted_asset_count, cost_per_accepted_asset
            FROM analytics.kpi_rollup_cost_per_accepted_asset
            WHERE day = $1
            ORDER BY agent_name
            """,
            day,
        )
        utilisation_rows = await conn.fetch(
            """
            SELECT day, kpi_name, object_class, caller_service, read_count
            FROM analytics.kpi_rollup_vault_utilisation
            WHERE day = $1
            ORDER BY object_class, caller_service
            """,
            day,
        )

    export = {
        "day": day.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "engagement_by_archetype": [_row_to_dict(r) for r in engagement_rows],
        "publishing_reliability": [_row_to_dict(r) for r in reliability_rows],
        "cost_per_accepted_asset": [_row_to_dict(r) for r in cost_rows],
        "vault_utilisation": [_row_to_dict(r) for r in utilisation_rows],
    }

    schema_path = contracts_dir() / "fabric-nightly-export.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    jsonschema.validate(export, schema)

    return export
