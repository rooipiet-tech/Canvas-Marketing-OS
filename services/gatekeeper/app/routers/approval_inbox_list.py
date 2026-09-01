"""GET /approval-inbox — the approval queue, for the console's inbox
screen (INTEG-002).

console/app/clients/gatekeeper_real.py has called this route since it was
written and this service never exposed it; its own docstring records the
finding ("a REST wrapper ... that does not exist yet"). Because the
console is pinned to GATEKEEPER_API_MODE 'mock', whose inbox starts empty
and is only ever filled by test seeding, /approvals reported "no
approvals pending" indefinitely while real rows accumulated in
governance.approval_inbox and runs blocked behind them.

That is the worst failure shape a governance screen has: not an error a
reader would investigate, but a confident, plausible "nothing is waiting
for you".

Read-only, and deliberately narrower than the table. `link_token` is
never returned: it is the single-use secret inside the Approve/Reject
deep link, so anyone holding it can decide the approval. A list view
needs to show what is pending, never to hand out the means to approve it.
The click surface remains the physically separate, Entra-protected
ca-gatekeeper-approval app (approval_main.py), exactly as before.

Lives on ca-gatekeeper (internal ingress) alongside /gate-check and
/approval-status, so it is reachable only from inside the VNet.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.approval_inbox import (
    STATUS_APPROVED,
    STATUS_EXPIRED,
    STATUS_PENDING,
    STATUS_REJECTED,
    list_inbox,
)
from app.db import get_conn

router = APIRouter(tags=["approval-inbox"])

ALLOWED_STATUSES = (STATUS_PENDING, STATUS_APPROVED, STATUS_REJECTED, STATUS_EXPIRED)

MAX_LIMIT = 200


def _serialise(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "gate_decision_id": (
            str(row["gate_decision_id"]) if row["gate_decision_id"] else None
        ),
        "agent_run_id": str(row["agent_run_id"]),
        "function_id": row["function_id"],
        "action_class": row["action_class"],
        "level": row["level"],
        "content_hash": row["content_hash"],
        "preview_title": row["preview_title"],
        "preview_reference": row["preview_reference"],
        "evidence_summary": row["evidence_summary"],
        "status": row["status"],
        "decided_by": row["decided_by"],
        "decided_at": row["decided_at"].isoformat() if row["decided_at"] else None,
        "expires_at": row["expires_at"].isoformat() if row["expires_at"] else None,
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
    }


@router.get("/approval-inbox")
def get_approval_inbox(
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=MAX_LIMIT),
    conn=Depends(get_conn),
) -> list[dict[str, Any]]:
    if status is not None and status not in ALLOWED_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"status must be one of {', '.join(ALLOWED_STATUSES)}",
        )
    return [_serialise(row) for row in list_inbox(conn, status=status, limit=limit)]
