"""Utilisation roll-up (AC-008, AC-021).

`record_access()` is called on every GET of an individual object resource
(vault/routers/objects.py), writing one vault_internal.access_log row
keyed by the calling service (X-Caller-Service header, default
"unknown"). `run_rollup()` upserts vault_internal.utilisation_daily —- a
real daily rollup TABLE, not just an aggregate view -- from access_log for
a given date. `read_rollup()` powers GET /utilisation/rollup with
from/to/object_class filtering.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import asyncpg


async def record_access(
    conn: asyncpg.Connection,
    *,
    object_table: str,
    object_id: str | None,
    caller_service: str | None,
) -> None:
    await conn.execute(
        """
        INSERT INTO vault_internal.access_log (object_table, object_id, caller_service)
        VALUES ($1, $2, $3)
        """,
        object_table,
        object_id,
        caller_service or "unknown",
    )


async def run_rollup(pool: asyncpg.Pool, *, day: date | None = None) -> int:
    day = day or datetime.now(timezone.utc).date()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT object_table, caller_service, count(*) AS read_count
            FROM vault_internal.access_log
            WHERE occurred_at::date = $1
            GROUP BY object_table, caller_service
            """,
            day,
        )
        for row in rows:
            await conn.execute(
                """
                INSERT INTO vault_internal.utilisation_daily
                    (day, object_table, caller_service, read_count)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (day, object_table, caller_service)
                DO UPDATE SET read_count = EXCLUDED.read_count, updated_at = now()
                """,
                day,
                row["object_table"],
                row["caller_service"],
                row["read_count"],
            )
        return len(rows)


async def read_rollup(
    pool: asyncpg.Pool,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    object_class: str | None = None,
) -> list[dict]:
    conditions = []
    params: list = []
    if date_from is not None:
        params.append(date_from)
        conditions.append(f"day >= ${len(params)}")
    if date_to is not None:
        params.append(date_to)
        conditions.append(f"day <= ${len(params)}")
    if object_class is not None:
        params.append(object_class)
        conditions.append(f"object_table = ${len(params)}")

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    query = f"""
        SELECT day, object_table, caller_service, read_count
        FROM vault_internal.utilisation_daily
        {where_clause}
        ORDER BY day, object_table, caller_service
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *params)
    return [
        {
            "date": row["day"].isoformat(),
            "object_class": row["object_table"],
            "caller_service": row["caller_service"],
            "read_count": row["read_count"],
        }
        for row in rows
    ]
