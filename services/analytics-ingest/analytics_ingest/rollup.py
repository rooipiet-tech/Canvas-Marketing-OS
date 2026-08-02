"""analytics_ingest.rollup — the 4 nightly KPI rollups.

Every function upserts via INSERT ... ON CONFLICT (day, kpi_name, ...) DO
UPDATE — rollups refresh cleanly on re-run (C-IDEMPOTENCY / AC-09).
"""

from __future__ import annotations

from datetime import date
from typing import Any

_CHANNEL_TABLE = {"buffer": "buffer_post_metrics", "linkedin": "linkedin_metrics"}


async def rollup_engagement_by_archetype(pool: Any, day: date) -> None:
    """SUM(reactions+comments+shares+clicks)/SUM(impressions), grouped by (source, post_archetype).

    Sources analytics.buffer_post_metrics and analytics.linkedin_metrics
    for `day` — the two tables carry an identical column shape and are
    combined via UNION ALL before grouping, so buffer and linkedin
    aggregate through the identical code path (see
    tests/test_kpi_rollups.py's documented rationale for why AC-19's
    golden-value assertion is scoped to source='buffer' only).

    (source, post_archetype) groups with SUM(impressions) NULL or 0 are
    skipped (no row written) — same div-by-zero-avoidance design choice as
    rollup_cost_per_accepted_asset's 0-accepted-asset skip below, applied
    via the HAVING clause rather than a Python-side check.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            WITH combined AS (
                SELECT source, post_archetype, reactions, comments, shares, clicks, impressions
                FROM analytics.buffer_post_metrics WHERE day = $1
                UNION ALL
                SELECT source, post_archetype, reactions, comments, shares, clicks, impressions
                FROM analytics.linkedin_metrics WHERE day = $1
            )
            SELECT
                source,
                post_archetype,
                (SUM(COALESCE(reactions, 0) + COALESCE(comments, 0) + COALESCE(shares, 0) + COALESCE(clicks, 0))::numeric
                    / NULLIF(SUM(impressions), 0)) AS engagement_rate,
                COUNT(*) AS post_count
            FROM combined
            WHERE post_archetype IS NOT NULL
            GROUP BY source, post_archetype
            HAVING SUM(impressions) > 0
            """,
            day,
        )

        for row in rows:
            await conn.execute(
                """
                INSERT INTO analytics.kpi_rollup_engagement_by_archetype
                    (day, kpi_name, source, post_archetype, engagement_rate, post_count)
                VALUES ($1, 'engagement_rate_by_post_archetype', $2, $3, $4, $5)
                ON CONFLICT (day, kpi_name, source, post_archetype) DO UPDATE SET
                    engagement_rate = EXCLUDED.engagement_rate,
                    post_count = EXCLUDED.post_count,
                    updated_at = now()
                """,
                day,
                row["source"],
                row["post_archetype"],
                row["engagement_rate"],
                row["post_count"],
            )


async def rollup_publishing_reliability(pool: Any, day: date) -> None:
    """published_count (per channel, from the raw fact tables) / scheduled_count (analytics.scheduled_posts).

    Channels with scheduled_count 0 are skipped (no row written) — same
    div-by-zero-avoidance design choice as rollup_cost_per_accepted_asset's
    0-accepted-asset skip.
    """
    async with pool.acquire() as conn:
        scheduled_rows = await conn.fetch(
            "SELECT channel, scheduled_count FROM analytics.scheduled_posts WHERE day = $1", day
        )

        for scheduled_row in scheduled_rows:
            channel = scheduled_row["channel"]
            table = _CHANNEL_TABLE.get(channel)
            if table is None:
                continue

            scheduled_count = scheduled_row["scheduled_count"]
            if not scheduled_count:
                continue

            # Defensive: table is only ever an f-string-interpolated value
            # here because it came from the fixed _CHANNEL_TABLE allowlist
            # above (never unvalidated input) — assert that invariant so it
            # can never silently stop holding after a future edit (RISK-04).
            assert table in set(_CHANNEL_TABLE.values())
            published_count = await conn.fetchval(
                f"SELECT COUNT(*) FROM analytics.{table} WHERE day = $1", day
            )
            reliability_rate = published_count / scheduled_count

            await conn.execute(
                """
                INSERT INTO analytics.kpi_rollup_publishing_reliability
                    (day, kpi_name, channel, scheduled_count, published_count, reliability_rate)
                VALUES ($1, 'publishing_reliability', $2, $3, $4, $5)
                ON CONFLICT (day, kpi_name, channel) DO UPDATE SET
                    scheduled_count = EXCLUDED.scheduled_count,
                    published_count = EXCLUDED.published_count,
                    reliability_rate = EXCLUDED.reliability_rate,
                    updated_at = now()
                """,
                day,
                channel,
                scheduled_count,
                published_count,
                reliability_rate,
            )


async def rollup_cost_per_accepted_asset(pool: Any, vault_client: Any, day: date) -> None:
    """total_cost_usd / accepted_asset_count, grouped by agent_runs.agent_name.

    Per C-COST-JOIN: costs JOIN agent_runs (agent_run_id) to attribute
    every cost entry to an agent_name, and assets JOIN agent_runs
    (agent_run_id, filtered assets.approval_state == 'approved') to count
    accepted assets per agent_name — not a nonexistent direct
    cost-to-asset FK. agent_names with 0 approved assets are skipped (no
    row written) — a documented div-by-zero-avoidance design choice, not
    an oversight: total_cost_by_agent_name may still hold a value for such
    an agent_name (e.g. brief-drafter, whose only asset was rejected), but
    accepted_count_by_agent_name never gains an entry for it by
    construction, so no row is ever emitted for it.
    """
    costs = await vault_client.get_costs()
    agent_runs = await vault_client.get_agent_runs()
    assets = await vault_client.get_assets()

    agent_run_to_name = {ar["id"]: ar["agent_name"] for ar in agent_runs}

    total_cost_by_agent_name: dict[str, float] = {}
    for cost in costs:
        agent_name = agent_run_to_name.get(cost.get("agent_run_id"))
        if agent_name is None:
            continue
        total_cost_by_agent_name[agent_name] = total_cost_by_agent_name.get(agent_name, 0.0) + float(
            cost["amount"]
        )

    accepted_count_by_agent_name: dict[str, int] = {}
    for asset in assets:
        if asset.get("approval_state") != "approved":
            continue
        agent_name = agent_run_to_name.get(asset.get("agent_run_id"))
        if agent_name is None:
            continue
        accepted_count_by_agent_name[agent_name] = accepted_count_by_agent_name.get(agent_name, 0) + 1

    async with pool.acquire() as conn:
        for agent_name, accepted_asset_count in accepted_count_by_agent_name.items():
            total_cost_usd = total_cost_by_agent_name.get(agent_name, 0.0)
            cost_per_accepted_asset = total_cost_usd / accepted_asset_count

            await conn.execute(
                """
                INSERT INTO analytics.kpi_rollup_cost_per_accepted_asset
                    (day, kpi_name, agent_name, total_cost_usd, accepted_asset_count, cost_per_accepted_asset)
                VALUES ($1, 'cost_per_accepted_asset', $2, $3, $4, $5)
                ON CONFLICT (day, kpi_name, agent_name) DO UPDATE SET
                    total_cost_usd = EXCLUDED.total_cost_usd,
                    accepted_asset_count = EXCLUDED.accepted_asset_count,
                    cost_per_accepted_asset = EXCLUDED.cost_per_accepted_asset,
                    updated_at = now()
                """,
                day,
                agent_name,
                total_cost_usd,
                accepted_asset_count,
                cost_per_accepted_asset,
            )


async def rollup_vault_utilisation(pool: Any, vault_client: Any, day: date) -> None:
    """1:1 upsert of Vault's own GET /utilisation/rollup rows — never reinvented from raw access logs."""
    rows = await vault_client.get_utilisation_rollup(str(day), str(day))

    async with pool.acquire() as conn:
        for row in rows:
            await conn.execute(
                """
                INSERT INTO analytics.kpi_rollup_vault_utilisation
                    (day, kpi_name, object_class, caller_service, read_count)
                VALUES ($1, 'vault_utilisation', $2, $3, $4)
                ON CONFLICT (day, kpi_name, object_class, caller_service) DO UPDATE SET
                    read_count = EXCLUDED.read_count,
                    updated_at = now()
                """,
                day,
                row["object_class"],
                row["caller_service"],
                row["read_count"],
            )
