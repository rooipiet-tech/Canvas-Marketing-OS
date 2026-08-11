"""Pydantic response models shared by every route (HTML + JSON) via
console/app/services.py — the single source of truth both surfaces render."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel


class TaskRow(BaseModel):
    id: str
    agent_name: str
    status: str
    campaign: str | None = None
    started_at: str | None = None


class ReviewDetail(BaseModel):
    """F-TEAMS-CARD-REVIEW-LINK: what GET /review/{task_id} renders --
    mirrors the JSON shape of ca-orchestrator's GET /tasks/{task_id}/review."""

    task_id: str
    task_type: str
    state: str
    retry_count: int
    result_ref: dict | None = None
    draft_text: str | None = None
    draft_error: str | None = None


class SpanRow(BaseModel):
    timestamp: str | None = None
    name: str | None = None
    function_id: str | None = None
    task_ref: str | None = None
    model: str | None = None
    registry_version: str | None = None
    cost: str | None = None


class ApprovalRow(BaseModel):
    id: str
    function_id: str
    action_class: str
    level: int
    preview_title: str
    status: str
    decided_by: str | None = None
    decided_at: str | None = None


class AssetRow(BaseModel):
    id: str
    asset_type: str
    approval_state: str
    vertical: str
    function_id: str
    campaign: str | None = None
    evidence_grade: str
    consent_status: str
    retention_class: str


class CostGroupRow(BaseModel):
    group_key: str
    total: Decimal


class KillSwitchAuditEntry(BaseModel):
    id: str | None = None
    active: bool
    reason: str | None = None
    operator: str
    decided_at: str | None = None


class KillSwitchState(BaseModel):
    active: bool
    reason: str | None = None
    scope: str = "global"
    # GOAL-004: the audit trail entry for the most recent toggle, exposed
    # over HTTP here (GET /kill-switch) since no other route surfaces it —
    # GatekeeperClient.get_last_audit_entry() was previously only reachable
    # at the Python object level. None before any toggle has ever happened.
    last_audit_entry: KillSwitchAuditEntry | None = None
