"""Retention-class expiry job (AC-007).

`run_retention_expiry()` is the single shared implementation used by both
the HTTP-triggered path (vault/routers/retention.py, POST
/retention-expiry-runs) and the standalone entrypoint below
(`python -m vault.retention`, used by
infra/modules/vault/retention-expiry-job.bicep's caj-vault-retention-expiry
Container Apps Job).

retention_class -> duration mapping (builder judgment call, documented
here and in contracts/vault-api.yaml's TaxonomyFields.retention_class
enum description):
  ephemeral_30d -> 30 days
  standard_1y   -> 365 days
  extended_3y   -> 1095 days
  legal_hold    -> never expires (expires_at is set far in the future so
                    it is never selected by the sweep below, rather than
                    NULL, so the NOT NULL constraint on
                    vault_internal.retention_policy.expires_at holds for
                    every row uniformly).
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime, timedelta, timezone

import asyncpg

from .audit import write_audit
from .models import OBJECT_TYPES
from .storage import delete_content_if_unreferenced

logger = logging.getLogger("vault.retention")

RETENTION_DURATIONS: dict[str, timedelta | None] = {
    "ephemeral_30d": timedelta(days=30),
    "standard_1y": timedelta(days=365),
    "extended_3y": timedelta(days=1095),
    "legal_hold": None,
}

LEGAL_HOLD_SENTINEL = datetime(9999, 12, 31, tzinfo=timezone.utc)


def compute_expires_at(retention_class: str, *, now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    duration = RETENTION_DURATIONS.get(retention_class)
    if duration is None:
        return LEGAL_HOLD_SENTINEL
    return now + duration


async def run_retention_expiry(pool: asyncpg.Pool) -> dict:
    """Sweeps every vault_internal.retention_policy row past its
    expires_at, deletes the underlying object (and, for assets, the
    underlying blob if no other live asset shares its content hash),
    and writes one audit_log row per deletion. Returns a summary dict."""
    async with pool.acquire() as conn:
        run_id = await conn.fetchval(
            "INSERT INTO vault_internal.retention_run (status) VALUES ('running') RETURNING id"
        )

        expired = await conn.fetch(
            """
            SELECT object_table, object_id, retention_class
            FROM vault_internal.retention_policy
            WHERE expires_at <= now() AND deleted_at IS NULL
            FOR UPDATE SKIP LOCKED
            """
        )

        deleted_count = 0
        for row in expired:
            object_table = row["object_table"]
            object_id = row["object_id"]
            retention_class = row["retention_class"]
            detail: dict = {"retention_class": retention_class}

            if object_table == "assets":
                asset = await conn.fetchrow(
                    "SELECT content_hash FROM assets WHERE id = $1", object_id
                )
                if asset and asset["content_hash"]:
                    content_hash = asset["content_hash"]
                    detail["content_hash"] = content_hash
                    await conn.execute("DELETE FROM assets WHERE id = $1", object_id)
                    still_referenced_row = await conn.fetchval(
                        "SELECT count(*) FROM assets WHERE content_hash = $1", content_hash
                    )
                    still_referenced = bool(still_referenced_row and still_referenced_row > 0)
                    try:
                        await asyncio.to_thread(
                            delete_content_if_unreferenced,
                            content_hash,
                            still_referenced=still_referenced,
                        )
                    except (
                        Exception
                    ) as exc:  # pragma: no cover - blob store may be unavailable in dev
                        logger.warning("blob delete failed for %s: %s", content_hash, exc)
                else:
                    await conn.execute("DELETE FROM assets WHERE id = $1", object_id)
            else:
                if object_table not in OBJECT_TYPES:
                    logger.warning("unknown object_table in retention_policy: %s", object_table)
                    continue
                await conn.execute(f"DELETE FROM {object_table} WHERE id = $1", object_id)

            await conn.execute(
                """
                UPDATE vault_internal.retention_policy
                SET deleted_at = now()
                WHERE object_table = $1 AND object_id = $2
                """,
                object_table,
                object_id,
            )

            await write_audit(
                conn,
                event_type="retention_deletion",
                object_table=object_table,
                object_id=str(object_id),
                reason=f"retention_class={retention_class} expired",
                detail=detail,
            )
            deleted_count += 1

        await conn.execute(
            """
            UPDATE vault_internal.retention_run
            SET status = 'succeeded', completed_at = now(), deleted_count = $2
            WHERE id = $1
            """,
            run_id,
            deleted_count,
        )

        return {
            "id": str(run_id),
            "status": "succeeded",
            "deleted_count": deleted_count,
        }


async def _main() -> None:
    from .db import close_pool, get_pool

    logging.basicConfig(level=logging.INFO)
    pool = await get_pool()
    try:
        summary = await run_retention_expiry(pool)
        print(summary, flush=True)
    finally:
        await close_pool()


if __name__ == "__main__":
    # Entrypoint for caj-vault-retention-expiry
    # (infra/modules/vault/retention-expiry-job.bicep): `python -m vault.retention`
    asyncio.run(_main())
    sys.exit(0)
