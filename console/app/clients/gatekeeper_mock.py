"""GatekeeperMock — MOCK, non-authoritative.

Mirrors session/s4-governance's uncommitted Gatekeeper API shape
(governance.approval_inbox / governance.kill_switches, observed read-only
this session). Ships only because session/s4-governance has not merged to
main yet (see INTEG-002). This module declares NO SQL, NO CREATE TABLE,
and NO governance schema of any kind — it is a purely in-memory,
process-local stand-in (SCOPE-004).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any


def _now() -> datetime:
    return datetime.now(timezone.utc)


class GatekeeperMock:
    """In-memory approval_inbox list + a single global kill-switch state."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._approval_inbox: list[dict[str, Any]] = []
        self._kill_switch_active: bool = False
        self._kill_switch_reason: str | None = None
        self._audit_log: list[dict[str, Any]] = []

    # --- seed helper --------------------------------------------------

    def seed_approval_inbox(self, **fields: Any) -> dict[str, Any]:
        record: dict[str, Any] = {
            "id": fields.pop("id", str(uuid.uuid4())),
            "agent_run_id": fields.pop("agent_run_id", str(uuid.uuid4())),
            "function_id": fields.pop("function_id", "unknown"),
            "action_class": fields.pop("action_class", "publish"),
            "level": fields.pop("level", 1),
            "preview_title": fields.pop("preview_title", "untitled"),
            "preview_reference": fields.pop("preview_reference", None),
            "evidence_summary": fields.pop("evidence_summary", ""),
            "status": fields.pop("status", "pending"),
            "decided_by": fields.pop("decided_by", None),
            "decided_at": fields.pop("decided_at", None),
            "created_at": fields.pop("created_at", _now().isoformat()),
        }
        record.update(fields)
        self._approval_inbox.append(record)
        return record

    # --- GatekeeperClient protocol methods -----------------------------

    async def list_approval_inbox(self) -> list[dict[str, Any]]:
        return list(self._approval_inbox)

    async def get_kill_switch_state(self) -> dict[str, Any]:
        return {
            "active": self._kill_switch_active,
            "reason": self._kill_switch_reason,
            "scope": "global",
        }

    async def toggle_kill_switch(
        self, active: bool, reason: str, operator: str
    ) -> dict[str, Any]:
        self._kill_switch_active = active
        self._kill_switch_reason = reason
        audit_entry = {
            "id": str(uuid.uuid4()),
            "active": active,
            "reason": reason,
            "operator": operator,
            "decided_at": _now().isoformat(),
        }
        self._audit_log.append(audit_entry)
        return await self.get_kill_switch_state()

    async def get_last_audit_entry(self) -> dict[str, Any] | None:
        return self._audit_log[-1] if self._audit_log else None
