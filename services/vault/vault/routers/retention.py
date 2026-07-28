"""POST/GET /retention-expiry-runs — see contracts/vault-api.yaml
triggerRetentionExpiry / listRetentionExpiryRuns / getRetentionExpiryRun.

The HTTP trigger runs the sweep inline and returns its result — the same
run_retention_expiry() shared function that
infra/modules/vault/retention-expiry-job.bicep's caj-vault-retention-expiry
Container Apps Job invokes via `python -m vault.retention` (AC-009: an
agent can trigger the job through either path)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query

from ..db import get_pool
from ..retention import run_retention_expiry

router = APIRouter()

_RUN_COLUMNS = "id, status, started_at, completed_at, deleted_count"
_RUN_SELECT_BY_ID = f"SELECT {_RUN_COLUMNS} FROM vault_internal.retention_run WHERE id = $1"


@router.post("/retention-expiry-runs", status_code=202)
async def trigger_retention_expiry():
    pool = await get_pool()
    summary = await run_retention_expiry(pool)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(_RUN_SELECT_BY_ID, uuid.UUID(summary["id"]))
    return dict(row)


@router.get("/retention-expiry-runs")
async def list_retention_expiry_runs(
    limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)
):
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, status, started_at, completed_at, deleted_count
            FROM vault_internal.retention_run
            ORDER BY started_at DESC
            LIMIT $1 OFFSET $2
            """,
            limit,
            offset,
        )
    return [dict(r) for r in rows]


@router.get("/retention-expiry-runs/{id}")
async def get_retention_expiry_run(id: uuid.UUID):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(_RUN_SELECT_BY_ID, id)
    if row is None:
        raise HTTPException(
            status_code=404, detail={"error": {"message": "not found", "code": "not_found"}}
        )
    return dict(row)
