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


class KillSwitchState(BaseModel):
    active: bool
    reason: str | None = None
    scope: str = "global"
