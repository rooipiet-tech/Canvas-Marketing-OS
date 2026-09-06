"""POST/GET /option-cards — see contracts/vault-api.yaml createOptionCard /
getOptionCard / listOptionCards (Appendix D PR 5).

option_cards is deliberately NOT one of vault/models.py's OBJECT_TYPES:
that generic factory serves the 9 frozen-v1 object types (contracts/
vault-schema/schema.sql), keyed uniformly on an `id` column and carrying
the 6 mandatory taxonomy fields. option_cards' primary key is `card_id`
(contracts/option-card.schema.json's own field name, not a generic `id`)
and its `card` jsonb column IS the full OptionCard document -- forcing it
through the taxonomy-fields shape would fight the contract it already
has, for no shared benefit. A small, purpose-built router (this file)
matches the existing precedent of retention.py/utilisation.py, which are
not object types either.

services/gatekeeper/app/option_decisions.py reads and writes
option_cards / approval_decisions directly against the same Postgres
(gatekeeper already holds a governance-schema connection there; no
reason to hop through this HTTP API for its own /decide endpoint). This
router exists for services/orchestrator, which — unlike gatekeeper —
never holds a direct Postgres connection and talks to Vault exclusively
over HTTP (orchestrator/clients/vault_client_ext.py)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import asyncpg
from fastapi import APIRouter, Body, HTTPException, Query

from ..db import get_pool

router = APIRouter()

_COLUMNS = (
    "card_id, kind, autonomy_level, risk_tier, agent_run_id, "
    "produced_by_function, card, created_at, expires_at"
)


def _not_found(card_id: object) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"error": {"message": f"option card {card_id} not found", "code": "not_found"}},
    )


@router.post("/option-cards", status_code=201)
async def create_option_card(payload: dict = Body(...)):
    required = ("kind", "autonomy_level", "risk_tier", "produced_by_function", "card", "expires_at")
    missing = [f for f in required if f not in payload]
    if missing:
        raise HTTPException(
            status_code=422,
            detail={"error": {"message": f"missing field(s): {missing}", "code": "invalid_body"}},
        )

    card_id = uuid.UUID(payload["card_id"]) if payload.get("card_id") else uuid.uuid4()
    created_at = (
        datetime.fromisoformat(payload["created_at"])
        if payload.get("created_at")
        else datetime.now(timezone.utc)
    )
    expires_at = datetime.fromisoformat(payload["expires_at"])
    agent_run_id = uuid.UUID(payload["agent_run_id"]) if payload.get("agent_run_id") else None

    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            row = await conn.fetchrow(
                f"""
                INSERT INTO option_cards ({_COLUMNS})
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                RETURNING {_COLUMNS}
                """,
                card_id,
                payload["kind"],
                payload["autonomy_level"],
                payload["risk_tier"],
                agent_run_id,
                payload["produced_by_function"],
                payload["card"],
                created_at,
                expires_at,
            )
        except asyncpg.ForeignKeyViolationError as exc:
            # Same convention as vault/routers/objects.py's own FK guard:
            # agent_run_id must reference a real agent_runs row (the
            # PR #105 lesson every option-card-emitting handler respects).
            raise HTTPException(
                status_code=422,
                detail={
                    "error": {
                        "message": f"referenced id does not exist: {exc}",
                        "code": "fk_violation",
                    }
                },
            ) from exc
    return dict(row)


@router.get("/option-cards/{card_id}")
async def get_option_card(card_id: uuid.UUID):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {_COLUMNS} FROM option_cards WHERE card_id = $1", card_id
        )
    if row is None:
        raise _not_found(card_id)
    return dict(row)


@router.get("/option-cards")
async def list_option_cards(
    pending: bool = Query(False, description="undecided AND unexpired only"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    pool = await get_pool()
    async with pool.acquire() as conn:
        if pending:
            rows = await conn.fetch(
                f"""
                SELECT {_COLUMNS} FROM option_cards oc
                WHERE oc.expires_at > now()
                  AND NOT EXISTS (
                    SELECT 1 FROM approval_decisions ad WHERE ad.card_id = oc.card_id
                  )
                ORDER BY oc.created_at ASC
                LIMIT $1 OFFSET $2
                """,
                limit,
                offset,
            )
        else:
            rows = await conn.fetch(
                f"""
                SELECT {_COLUMNS} FROM option_cards
                ORDER BY created_at DESC
                LIMIT $1 OFFSET $2
                """,
                limit,
                offset,
            )
    return [dict(r) for r in rows]


_DECISION_HISTORY_COLUMNS = (
    "ad.card_id, oc.kind, oc.produced_by_function, oc.autonomy_level, oc.card, "
    "ad.outcome, ad.chosen_option_id, ad.was_recommended, ad.rejection_code, "
    "ad.decided_by, ad.decided_at, ad.latency_seconds, ad.channel"
)


@router.get("/decision-history")
async def list_decision_history(
    since: datetime | None = Query(
        None, description="Only decisions with decided_at >= this timestamp."
    ),
    limit: int = Query(500, ge=1, le=2000),
    offset: int = Query(0, ge=0),
):
    """Fn 126 (Decision Quality Evaluator, Appendix D PR 6/7) is the first
    caller: it needs the learning signal contracts/approval-decision.
    schema.json describes (was_recommended -> Recommendation Hit Rate,
    rejection_code -> the histogram) joined with the PRODUCING function
    (option_cards.produced_by_function), which approval_decisions alone
    does not carry. Neither GET /option-cards (no join to approval_
    decisions at all, decided or not) nor any other existing endpoint
    exposes this -- orchestrator has no direct Postgres connection (see
    this file's own module docstring), so a new read endpoint is the only
    way it can ever compute a real metric here. Additive: vault-api.yaml
    is not one of the 7 frozen contracts (this file's own header explains
    why option_cards/approval_decisions aren't in the frozen schema
    either), so a new GET-only endpoint needs no version bump."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        if since is not None:
            rows = await conn.fetch(
                f"""
                SELECT {_DECISION_HISTORY_COLUMNS}
                FROM approval_decisions ad
                JOIN option_cards oc ON oc.card_id = ad.card_id
                WHERE ad.decided_at >= $1
                ORDER BY ad.decided_at DESC
                LIMIT $2 OFFSET $3
                """,
                since,
                limit,
                offset,
            )
        else:
            rows = await conn.fetch(
                f"""
                SELECT {_DECISION_HISTORY_COLUMNS}
                FROM approval_decisions ad
                JOIN option_cards oc ON oc.card_id = ad.card_id
                ORDER BY ad.decided_at DESC
                LIMIT $1 OFFSET $2
                """,
                limit,
                offset,
            )
    return [dict(r) for r in rows]
