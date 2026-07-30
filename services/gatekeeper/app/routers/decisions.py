"""gate_decisions read API + the single INSERT helper (AC-03, AC-17).

gate_decisions is APPEND-ONLY. `insert_gate_decision` is the only write
path in this service and it only ever INSERTs; there is no UPDATE and no
DELETE against gate_decisions anywhere in Gatekeeper or Publisher. A
re-decision (e.g. a human approving after an earlier escalation) is a NEW
row referencing the same agent_run_id, per the frozen schema's documented
convention.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.db import get_conn

router = APIRouter(tags=["decisions"])

VALID_OUTCOMES = ("approved", "rejected", "escalated")

_INSERT_DECISION = """
    INSERT INTO gate_decisions (agent_run_id, decided_by, outcome, reason)
    VALUES (%(agent_run_id)s, %(decided_by)s, %(outcome)s, %(reason)s)
    RETURNING id, agent_run_id, decided_by, outcome, reason, decided_at, created_at
"""

_SELECT_DECISION = """
    SELECT id, agent_run_id, decided_by, outcome, reason, decided_at, created_at
      FROM gate_decisions
     WHERE id = %(decision_id)s
"""


def insert_gate_decision(
    conn,
    *,
    agent_run_id: str | uuid.UUID,
    decided_by: str,
    outcome: str,
    reason: str | None,
) -> dict[str, Any]:
    """Append exactly one immutable gate_decisions row."""
    if outcome not in VALID_OUTCOMES:
        raise ValueError(f"outcome must be one of {VALID_OUTCOMES}, got {outcome!r}")
    with conn.cursor() as cur:
        cur.execute(
            _INSERT_DECISION,
            {
                "agent_run_id": str(agent_run_id),
                "decided_by": decided_by,
                "outcome": outcome,
                "reason": reason,
            },
        )
        return dict(cur.fetchone())


def fetch_gate_decision(conn, decision_id: str | uuid.UUID) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(_SELECT_DECISION, {"decision_id": str(decision_id)})
        row = cur.fetchone()
    return dict(row) if row else None


def _serialise(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "agent_run_id": str(row["agent_run_id"]),
        "decided_by": row["decided_by"],
        "outcome": row["outcome"],
        "reason": row["reason"],
        "decided_at": row["decided_at"].isoformat() if row["decided_at"] else None,
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
    }


@router.get("/decisions/{decision_id}")
def get_decision(decision_id: str, conn=Depends(get_conn)) -> dict[str, Any]:
    """Agent-native read-back of a decision (AC-17)."""
    try:
        uuid.UUID(decision_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="decision_id must be a uuid") from exc

    row = fetch_gate_decision(conn, decision_id)
    if row is None:
        raise HTTPException(status_code=404, detail="decision not found")
    return _serialise(row)
