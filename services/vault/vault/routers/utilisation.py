"""POST/GET /utilisation/rollup — see contracts/vault-api.yaml
runUtilisationRollup / getUtilisationRollup (AC-008, AC-021)."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Body, Query

from ..db import get_pool
from ..rollup import read_rollup, run_rollup

router = APIRouter()


@router.post("/utilisation/rollup")
async def run_utilisation_rollup(payload: dict | None = Body(default=None)):
    target_date = None
    if payload and payload.get("date"):
        target_date = date.fromisoformat(payload["date"])
    pool = await get_pool()
    rows_upserted = await run_rollup(pool, day=target_date)
    return {
        "date": (target_date or date.today()).isoformat(),
        "rows_upserted": rows_upserted,
    }


@router.get("/utilisation/rollup")
async def get_utilisation_rollup(
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = Query(default=None),
    object_class: str | None = Query(default=None),
):
    pool = await get_pool()
    rows = await read_rollup(
        pool,
        date_from=date.fromisoformat(from_) if from_ else None,
        date_to=date.fromisoformat(to) if to else None,
        object_class=object_class,
    )
    return rows
