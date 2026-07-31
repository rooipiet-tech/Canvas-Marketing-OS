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

from .audit import write_audit, write_audit_isolated
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

# vault_internal.access_log has no retention/cleanup of its own (RS-04) —
# it is pure request-telemetry (object_table, caller_service, timestamp,
# no personal data), already rolled up daily into utilisation_daily, so a
# straightforward age-based purge (not itself audited — this is
# housekeeping on an internal telemetry table, not a POPIA-relevant
# object deletion) is folded into the retention sweep's scope below.
ACCESS_LOG_RETENTION = timedelta(days=90)


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
    and writes one audit_log row per deletion. Returns a summary dict.

    Three correctness properties, each deliberate (see .loop/review.json
    F3/migration F1/F2 and .loop/lenses.json data_residency F-3):

    1. Fails CLOSED on blob-delete errors: if deleting the underlying
       blob raises, the object row is NOT deleted, retention_policy's
       deleted_at is NOT set (the row stays eligible for retry on the
       next sweep), and a 'retention_deletion_failed' audit event is
       written instead of a success 'retention_deletion' row.
    2. Atomic per object: each object's DELETE + retention_policy
       deleted_at UPDATE + success audit write happen inside one
       `async with conn.transaction():` savepoint, so a crash mid-sequence
       can never leave a permanently-deleted, un-audited object.
    3. SELECT ... FOR UPDATE SKIP LOCKED runs inside an explicit OUTER
       transaction spanning the whole batch (not auto-committed as its
       own single-statement transaction), so the row locks it acquires
       are actually held while this sweep processes the batch — otherwise
       asyncpg auto-commits the SELECT immediately, releasing the locks
       at once and defeating SKIP LOCKED's entire purpose of stopping two
       concurrent sweeps from double-processing the same rows. Choice
       made here: hold the outer transaction (and its row locks) for the
       full batch rather than re-SELECT-ing per row, because this job is
       a low-concurrency scheduled sweep (normally one execution at a
       time) where guaranteeing no double-processing of irreversible
       deletes matters far more than shortening lock hold time. Per-row
       atomicity (property 2) is still achieved via nested
       transactions/savepoints on the same connection within this one
       outer transaction.
    """
    async with pool.acquire() as conn:
        run_id = await conn.fetchval(
            "INSERT INTO vault_internal.retention_run (status) VALUES ('running') RETURNING id"
        )

        deleted_count = 0
        failed_count = 0

        async with conn.transaction():
            expired = await conn.fetch(
                """
                SELECT object_table, object_id, retention_class
                FROM vault_internal.retention_policy
                WHERE expires_at <= now() AND deleted_at IS NULL
                FOR UPDATE SKIP LOCKED
                """
            )

            for row in expired:
                object_table = row["object_table"]
                object_id = row["object_id"]
                retention_class = row["retention_class"]
                detail: dict = {"retention_class": retention_class}

                if object_table != "assets" and object_table not in OBJECT_TYPES:
                    logger.warning("unknown object_table in retention_policy: %s", object_table)
                    continue

                blob_delete_failed = False
                if object_table == "assets":
                    asset = await conn.fetchrow(
                        "SELECT content_hash FROM assets WHERE id = $1", object_id
                    )
                    content_hash = asset["content_hash"] if asset else None
                    if content_hash:
                        detail["content_hash"] = content_hash
                        # Exclude this row itself so a not-yet-deleted
                        # asset doesn't count as "still referencing" its
                        # own content hash.
                        still_referenced_row = await conn.fetchval(
                            "SELECT count(*) FROM assets WHERE content_hash = $1 AND id != $2",
                            content_hash,
                            object_id,
                        )
                        still_referenced = bool(still_referenced_row and still_referenced_row > 0)
                        try:
                            await asyncio.to_thread(
                                delete_content_if_unreferenced,
                                content_hash,
                                still_referenced=still_referenced,
                            )
                        except Exception as exc:  # noqa: BLE001 - blob store may be unavailable
                            logger.warning("blob delete failed for %s: %s", content_hash, exc)
                            blob_delete_failed = True

                if blob_delete_failed:
                    await write_audit_isolated(
                        event_type="retention_deletion_failed",
                        object_table=object_table,
                        object_id=str(object_id),
                        reason=(
                            f"retention_class={retention_class} expired but blob delete "
                            "failed; object left in place for retry on next sweep"
                        ),
                        detail=detail,
                    )
                    failed_count += 1
                    continue  # deliberately do NOT touch the object row or
                    # retention_policy.deleted_at -- eligible for retry.

                async with conn.transaction():
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

        # Housekeeping, not itself a retention_policy-governed deletion:
        # purge access_log rows past ACCESS_LOG_RETENTION (RS-04). This
        # runs as its own statement outside the batch transaction above —
        # it's telemetry cleanup, not an individually-audited object
        # deletion, so it doesn't need the same per-row atomicity/audit
        # treatment as the sweep itself.
        access_log_cutoff = datetime.now(timezone.utc) - ACCESS_LOG_RETENTION
        purged_access_log_rows = await conn.fetchval(
            """
            WITH purged AS (
                DELETE FROM vault_internal.access_log
                WHERE occurred_at < $1
                RETURNING 1
            )
            SELECT count(*) FROM purged
            """,
            access_log_cutoff,
        )

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
            "failed_count": failed_count,
            "access_log_rows_purged": purged_access_log_rows or 0,
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
