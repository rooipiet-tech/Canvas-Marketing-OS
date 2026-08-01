"""GET /approval-status — agent-native read of the REAL human decision
(plan step 4, AC-15).

Distinct from GET /decisions/{decision_id} (decisions.py): a gate_decision
row's own `outcome` is 'escalated' the instant /gate-check creates the
approval_inbox row — it never itself becomes 'approved'/'rejected' after
the human acts (a NEW gate_decisions row is only inserted on the NEXT
/gate-check call, per gate_check.py's own docstring). This endpoint reads
governance.approval_inbox directly instead, which IS mutated in place by
the approval-action click (app/approval_inbox.py's consume_link), so it is
the one place that reports pending/approved/rejected/expired truthfully
without a second /gate-check round trip.

Reachable with a plain agent_run_id + function_id (+ optional
content_hash) — no browser, no Entra session (this endpoint lives on
ca-gatekeeper, the internal-only app; the Entra-protected approval CLICK
surface is the physically separate ca-gatekeeper-approval app, main.py's
own docstring).
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.approval_inbox import latest_status
from app.db import get_conn

router = APIRouter(tags=["approval-status"])


def _serialise(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": row["status"],
        "decided_by": row["decided_by"],
        "decided_at": row["decided_at"].isoformat() if row["decided_at"] else None,
        "approval_inbox_id": str(row["id"]),
        "gate_decision_id": str(row["gate_decision_id"]) if row["gate_decision_id"] else None,
    }


@router.get("/approval-status")
def get_approval_status(
    agent_run_id: str = Query(...),
    function_id: str = Query(...),
    content_hash: str | None = Query(default=None),
    conn=Depends(get_conn),
) -> dict[str, Any]:
    try:
        uuid.UUID(agent_run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="agent_run_id must be a uuid") from exc

    row = latest_status(
        conn, agent_run_id=agent_run_id, function_id=function_id, content_hash=content_hash
    )
    if row is None:
        return {
            "status": "not_found",
            "decided_by": None,
            "decided_at": None,
            "approval_inbox_id": None,
            "gate_decision_id": None,
        }
    return _serialise(row)
