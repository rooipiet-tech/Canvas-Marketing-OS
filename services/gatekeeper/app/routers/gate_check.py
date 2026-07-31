"""POST /gate-check — the autonomy decision path (AC-02, AC-03, AC-12).

Exactly ONE gate_decisions row is appended on every branch:

  kill switch active    -> rejected   reason kill_switch_active:<scope>[:fn]
  level 0               -> rejected   reason level_0_blocked
  level 1, no approval  -> escalated  reason level_1_requires_approval
  level 2, no approval  -> escalated  reason level_2_requires_approval
  level 1/2, approved   -> approved   reason level_{1,2}_approved_by_human
  level 3               -> approved   reason level_3_auto_approved
  level 4               -> approved   reason level_4_autonomous_passthrough

A gate token is issued only on an `approved` outcome.

The kill-switch check is FIRST and is an uncached SELECT against the live
database on every single request (see app/kill_switch.py) — no cache of
any TTL sits in front of it.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.approval_inbox import (
    APPROVAL_ROUTE_INBOX,
    CHOICE_APPROVE,
    CHOICE_REJECT,
    approval_action_url,
    create_approval_request,
    dispatch_approval_request,
    latest_approved,
)
from app.db import get_conn
from app.kill_switch import is_blocked
from app.policy_loader import get_policy
from app.routers.decisions import insert_gate_decision
from app.signer import get_signer
from app.telemetry_wiring import emit_span
from app.tokens import issue_gate_token

router = APIRouter(tags=["gate-check"])

LEVEL_0_BLOCKED = "level_0_blocked"
LEVEL_1_REQUIRES_APPROVAL = "level_1_requires_approval"
LEVEL_2_REQUIRES_APPROVAL = "level_2_requires_approval"
LEVEL_3_AUTO_APPROVED = "level_3_auto_approved"
LEVEL_4_PASSTHROUGH = "level_4_autonomous_passthrough"

APPROVAL_REQUIRED_REASONS = {
    1: LEVEL_1_REQUIRES_APPROVAL,
    2: LEVEL_2_REQUIRES_APPROVAL,
}

DECIDED_BY_POLICY = "gatekeeper:policy"
DECIDED_BY_KILL_SWITCH = "gatekeeper:kill-switch"


class GateCheckRequest(BaseModel):
    agent_run_id: str
    function_id: str
    action_class: str
    content_hash: str | None = None
    preview_title: str | None = None
    preview_reference: str | None = None
    evidence_summary: str | None = None
    subject: str | None = Field(
        default=None,
        description="JWT `sub` — defaults to agent_run_id when omitted.",
    )


class GateCheckResponse(BaseModel):
    decision_id: str
    agent_run_id: str
    outcome: str
    reason: str
    level: int
    function_id: str
    action_class: str
    gate_token: str | None = None
    approval_route: str | None = None
    approval_id: str | None = None
    approve_url: str | None = None
    reject_url: str | None = None
    approval_expires_at: str | None = None


def _preview_title(request: GateCheckRequest) -> str:
    return request.preview_title or f"{request.function_id} ({request.action_class})"


def _evidence_summary(request: GateCheckRequest) -> str:
    if request.evidence_summary:
        return request.evidence_summary
    return (
        f"Autonomy policy requires human approval for {request.function_id} "
        f"/ {request.action_class}. Bound content hash: "
        f"{request.content_hash or '(none supplied)'}."
    )


def _decision_response(
    decision: dict[str, Any],
    *,
    level: int,
    request: GateCheckRequest,
    gate_token: str | None = None,
    approval: dict[str, Any] | None = None,
    approval_route: str | None = None,
) -> GateCheckResponse:
    return GateCheckResponse(
        decision_id=str(decision["id"]),
        agent_run_id=str(decision["agent_run_id"]),
        outcome=decision["outcome"],
        reason=decision["reason"],
        level=level,
        function_id=request.function_id,
        action_class=request.action_class,
        gate_token=gate_token,
        approval_route=approval_route,
        approval_id=str(approval["id"]) if approval else None,
        approve_url=(
            approval_action_url(approval["link_token"], CHOICE_APPROVE) if approval else None
        ),
        reject_url=(
            approval_action_url(approval["link_token"], CHOICE_REJECT) if approval else None
        ),
        approval_expires_at=(approval["expires_at"].isoformat() if approval else None),
    )


@router.post("/gate-check", response_model=GateCheckResponse)
def gate_check(
    request: GateCheckRequest, http_request: Request, conn=Depends(get_conn)
) -> GateCheckResponse:
    """Thin telemetry-wrapping shell (AC-03/AC-04/DE-5) around
    _gate_check_impl, which carries the full pre-existing decision logic
    unchanged. Kept as a separate wrapper (rather than inlining the span
    into the existing function body) so this adoption touches zero lines
    of the actual decision logic."""
    with emit_span(
        "gatekeeper.gate-check",
        http_request.headers,
        function_id=request.function_id,
        task_ref=request.agent_run_id,
    ):
        return _gate_check_impl(request, conn)


def _gate_check_impl(request: GateCheckRequest, conn) -> GateCheckResponse:
    try:
        uuid.UUID(request.agent_run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="agent_run_id must be a uuid") from exc

    policy = get_policy()
    level = policy.level_for(request.function_id, request.action_class)

    # (1) Kill switch — uncached live read, checked before anything else.
    status = is_blocked(conn, request.function_id)
    if status.blocked:
        decision = insert_gate_decision(
            conn,
            agent_run_id=request.agent_run_id,
            decided_by=DECIDED_BY_KILL_SWITCH,
            outcome="rejected",
            reason=status.audit_reason,
        )
        return _decision_response(decision, level=level, request=request)

    # (2) Level 0 — blocked always, no approval can unblock it.
    if level == 0:
        decision = insert_gate_decision(
            conn,
            agent_run_id=request.agent_run_id,
            decided_by=DECIDED_BY_POLICY,
            outcome="rejected",
            reason=LEVEL_0_BLOCKED,
        )
        return _decision_response(decision, level=level, request=request)

    # (3) Levels 1/2 — approval required.
    if level in APPROVAL_REQUIRED_REASONS:
        prior = latest_approved(
            conn,
            agent_run_id=request.agent_run_id,
            function_id=request.function_id,
            content_hash=request.content_hash,
        )
        if prior is None:
            decision = insert_gate_decision(
                conn,
                agent_run_id=request.agent_run_id,
                decided_by=DECIDED_BY_POLICY,
                outcome="escalated",
                reason=APPROVAL_REQUIRED_REASONS[level],
            )
            approval = create_approval_request(
                conn,
                gate_decision_id=decision["id"],
                agent_run_id=request.agent_run_id,
                function_id=request.function_id,
                action_class=request.action_class,
                level=level,
                content_hash=request.content_hash,
                preview_title=_preview_title(request),
                preview_reference=request.preview_reference,
                evidence_summary=_evidence_summary(request),
            )
            route = dispatch_approval_request(approval)
            return _decision_response(
                decision,
                level=level,
                request=request,
                approval=approval,
                approval_route=route or APPROVAL_ROUTE_INBOX,
            )

        reason = f"level_{level}_approved_by_human"
        decided_by = prior["decided_by"] or "unknown"
    elif level == 3:
        reason = LEVEL_3_AUTO_APPROVED
        decided_by = DECIDED_BY_POLICY
    elif level == 4:
        reason = LEVEL_4_PASSTHROUGH
        decided_by = DECIDED_BY_POLICY
    else:  # pragma: no cover - policy_loader already bounds level to 0..4
        raise HTTPException(status_code=500, detail=f"unhandled autonomy level {level}")

    decision = insert_gate_decision(
        conn,
        agent_run_id=request.agent_run_id,
        decided_by=decided_by,
        outcome="approved",
        reason=reason,
    )
    token, _claims = issue_gate_token(
        get_signer(),
        gate_decision_id=decision["id"],
        subject=request.subject or request.agent_run_id,
        content_hash=request.content_hash or "",
        function_id=request.function_id,
    )
    return _decision_response(decision, level=level, request=request, gate_token=token)
