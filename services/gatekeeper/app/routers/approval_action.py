"""GET /approval-action/{link_token} — the ONLY externally reachable
governance route (AC-30, AC-32, AC-33, AC-34).

Security model:

  * The route is mounted on approval_main.py only, which is served by
    ca-gatekeeper-approval (ingress external:true) behind Container Apps
    built-in Entra ID authentication. Everything else in Gatekeeper is
    internal-only ingress.
  * The approver is the Easy-Auth-authenticated principal on THIS request
    (app/auth.py). Holding the link proves nothing about identity — an
    unauthenticated request is rejected 401 before the token is even
    looked up.
  * Links are SINGLE-USE: consumption is an atomic conditional UPDATE
    (`... AND link_consumed_at IS NULL`), so two concurrent clicks cannot
    both win.
  * Links EXPIRE 24h after issuance.
  * All four click outcomes — approved, rejected, link_expired,
    link_already_used — append one governance.approval_actions audit row
    with a distinguishing `reason`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.approval_inbox import (
    CHOICE_APPROVE,
    OUTCOME_APPROVED,
    OUTCOME_LINK_ALREADY_USED,
    OUTCOME_LINK_EXPIRED,
    OUTCOME_REJECTED,
    STATUS_APPROVED,
    STATUS_REJECTED,
    consume_link,
    get_by_link_token,
    mark_expired,
    record_approval_action,
)
from app.auth import principal_from_headers
from app.db import get_conn
from app.routers.decisions import insert_gate_decision

router = APIRouter(tags=["approval-action"])

REASON_APPROVED = "approval_link_approved"
REASON_REJECTED = "approval_link_rejected"
REASON_LINK_EXPIRED = "link_expired"
REASON_LINK_ALREADY_USED = "link_already_used"


def _require_principal(request: Request):
    """No Easy-Auth principal on this request => no approval, ever."""
    principal = principal_from_headers(request.headers)
    if principal is None:
        raise HTTPException(
            status_code=401,
            detail=(
                "no authenticated principal on this request — the approver is "
                "taken from the Entra ID sign-in, never from possession of the link"
            ),
        )
    return principal


def _response(
    *,
    outcome: str,
    reason: str,
    approval_id: str | None,
    decision_id: str | None,
    decided_by: str | None,
) -> dict[str, Any]:
    return {
        "outcome": outcome,
        "reason": reason,
        "approval_id": approval_id,
        "decision_id": decision_id,
        "decided_by": decided_by,
    }


@router.get("/approval-action/{link_token}")
def approval_action(
    link_token: str,
    request: Request,
    choice: str = Query(..., pattern="^(approve|reject)$"),
    conn=Depends(get_conn),
) -> dict[str, Any]:
    principal = _require_principal(request)

    approval = get_by_link_token(conn, link_token)
    if approval is None:
        # Unknown token: nothing to bind an audit row to, and disclosing
        # more would turn this into a token oracle.
        raise HTTPException(status_code=404, detail="approval link not found")

    # (1) Already consumed -> replayed link.
    if approval["link_consumed_at"] is not None:
        record_approval_action(
            conn,
            approval_inbox_id=approval["id"],
            gate_decision_id=approval["gate_decision_id"],
            outcome=OUTCOME_LINK_ALREADY_USED,
            reason=REASON_LINK_ALREADY_USED,
            principal_id=principal.id,
            principal_name=principal.name,
        )
        raise HTTPException(
            status_code=409,
            detail=_response(
                outcome=OUTCOME_LINK_ALREADY_USED,
                reason=REASON_LINK_ALREADY_USED,
                approval_id=str(approval["id"]),
                decision_id=(
                    str(approval["gate_decision_id"]) if approval["gate_decision_id"] else None
                ),
                decided_by=approval["decided_by"],
            ),
        )

    # (2) Expired -> link_expired.
    if approval["expires_at"] <= datetime.now(timezone.utc):
        mark_expired(conn, approval["id"])
        record_approval_action(
            conn,
            approval_inbox_id=approval["id"],
            gate_decision_id=approval["gate_decision_id"],
            outcome=OUTCOME_LINK_EXPIRED,
            reason=REASON_LINK_EXPIRED,
            principal_id=principal.id,
            principal_name=principal.name,
        )
        raise HTTPException(
            status_code=410,
            detail=_response(
                outcome=OUTCOME_LINK_EXPIRED,
                reason=REASON_LINK_EXPIRED,
                approval_id=str(approval["id"]),
                decision_id=None,
                decided_by=None,
            ),
        )

    approved = choice == CHOICE_APPROVE
    outcome = OUTCOME_APPROVED if approved else OUTCOME_REJECTED
    reason = REASON_APPROVED if approved else REASON_REJECTED

    # (3) Atomically consume the single-use link. Losing this race means
    # another request consumed it first -> treat as replay.
    consumed = consume_link(
        conn,
        approval["id"],
        status=STATUS_APPROVED if approved else STATUS_REJECTED,
        decided_by=principal.decided_by,
    )
    if consumed is None:
        record_approval_action(
            conn,
            approval_inbox_id=approval["id"],
            gate_decision_id=approval["gate_decision_id"],
            outcome=OUTCOME_LINK_ALREADY_USED,
            reason=REASON_LINK_ALREADY_USED,
            principal_id=principal.id,
            principal_name=principal.name,
        )
        raise HTTPException(
            status_code=409,
            detail=_response(
                outcome=OUTCOME_LINK_ALREADY_USED,
                reason=REASON_LINK_ALREADY_USED,
                approval_id=str(approval["id"]),
                decision_id=None,
                decided_by=None,
            ),
        )

    # (4) Append the human decision. decided_by is the AUTHENTICATED
    #     principal on this request, resolved above — never a cached value
    #     and never derived from the link.
    decision = insert_gate_decision(
        conn,
        agent_run_id=approval["agent_run_id"],
        decided_by=principal.decided_by,
        outcome="approved" if approved else "rejected",
        reason=reason,
    )
    # Back-link the decision onto the (now consumed) inbox row.
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE governance.approval_inbox SET gate_decision_id = %s WHERE id = %s",
            (str(decision["id"]), str(approval["id"])),
        )

    record_approval_action(
        conn,
        approval_inbox_id=approval["id"],
        gate_decision_id=decision["id"],
        outcome=outcome,
        reason=reason,
        principal_id=principal.id,
        principal_name=principal.name,
    )

    return _response(
        outcome=outcome,
        reason=reason,
        approval_id=str(approval["id"]),
        decision_id=str(decision["id"]),
        decided_by=principal.decided_by,
    )
