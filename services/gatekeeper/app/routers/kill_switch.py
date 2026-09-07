"""GET/POST /kill-switch, GET /kill-switch/audit/last — the REST wrapper
around governance.kill_switches that TD-10 documents as missing.

console/app/clients/gatekeeper_real.py has called these three routes
since it was written, and this service never exposed any of them — the
only thing anything ever did with governance.kill_switches was a raw
`is_blocked()` read (app/kill_switch.py, checked on every /gate-check) and
a handful of direct test-fixture INSERTs. `GATEKEEPER_API_MODE` in
`infra/modules/console/console-app.bicep` was flipped to 'real' when
GET /approval-inbox shipped (INTEG-002), WITHOUT these routes existing —
so the console's kill-switch screen has been calling a 404 in the
meantime. This file, plus the mode flip already in place, is what closes
that gap; see console/README.md and docs/architecture/09-technical-debt.md
TD-10 for the fuller history.

SCOPE: the GLOBAL switch only (scope='global'), matching the console's
own `GatekeeperClient` protocol (`get_kill_switch_state()` /
`toggle_kill_switch()` take no scope/function_id argument) and
`KillSwitchState`'s hardcoded `scope: str = "global"` default.
`governance.kill_switches` also supports per-function switches
(scope='function'); this REST surface deliberately does not read or
write them — console/README.md already flags that as future scope for
whoever builds a function-scoped console screen, not a defect here.

ONE ROW PER GLOBAL SWITCH, UPDATED IN PLACE
--------------------------------------------
Unlike gate_decisions/approval_actions (append-only, enforced by
convention), kill_switches carries `updated_at` — it models CURRENT
state, not history. `_toggle_global` finds the existing global row (if
any) and UPDATEs it; only the very first toggle ever made INSERTs. That
single row is therefore also the "last audit entry" GET
/kill-switch/audit/last reports (`decided_by`/`updated_at`) — no separate
audit table. is_blocked() (app/kill_switch.py) is untouched: it already
tolerates any number of historical rows and is read-only here.

This intentionally does NOT wrap the read-then-write in an explicit
transaction/row lock — a kill-switch toggle is a rare, human-initiated
admin action, not a hot path, and every other write path in this service
(create_approval_request, insert_gate_decision) is a single autocommit
statement with the same lack of cross-request locking.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.db import get_conn

router = APIRouter(tags=["kill-switch"])

GLOBAL_SCOPE = "global"

_SELECT_GLOBAL = """
    SELECT id, active, reason, decided_by, updated_at
      FROM governance.kill_switches
     WHERE scope = 'global'
     ORDER BY created_at DESC
     LIMIT 1
"""

_SELECT_GLOBAL_ID = """
    SELECT id
      FROM governance.kill_switches
     WHERE scope = 'global'
     ORDER BY created_at DESC
     LIMIT 1
"""

_UPDATE_GLOBAL = """
    UPDATE governance.kill_switches
       SET active = %(active)s,
           reason = %(reason)s,
           decided_by = %(decided_by)s,
           updated_at = now()
     WHERE id = %(id)s
    RETURNING id, active, reason, decided_by, updated_at
"""

_INSERT_GLOBAL = """
    INSERT INTO governance.kill_switches (scope, active, reason, decided_by)
    VALUES ('global', %(active)s, %(reason)s, %(decided_by)s)
    RETURNING id, active, reason, decided_by, updated_at
"""


class KillSwitchToggleRequest(BaseModel):
    active: bool
    reason: str
    operator: str


def _get_global_state(conn) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(_SELECT_GLOBAL)
        row = cur.fetchone()
    return dict(row) if row else None


def _toggle_global(conn, *, active: bool, reason: str, decided_by: str) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(_SELECT_GLOBAL_ID)
        existing = cur.fetchone()
        params = {"active": active, "reason": reason, "decided_by": decided_by}
        if existing is not None:
            cur.execute(_UPDATE_GLOBAL, {**params, "id": existing["id"]})
        else:
            cur.execute(_INSERT_GLOBAL, params)
        return dict(cur.fetchone())


def _state_body(row: dict[str, Any] | None) -> dict[str, Any]:
    if row is None:
        return {"active": False, "reason": None, "scope": GLOBAL_SCOPE}
    return {"active": row["active"], "reason": row["reason"], "scope": GLOBAL_SCOPE}


def _audit_body(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "active": row["active"],
        "reason": row["reason"],
        "operator": row["decided_by"],
        "decided_at": row["updated_at"].isoformat() if row["updated_at"] else None,
    }


@router.get("/kill-switch")
def get_kill_switch(conn=Depends(get_conn)) -> dict[str, Any]:
    return _state_body(_get_global_state(conn))


@router.post("/kill-switch/toggle")
def post_kill_switch_toggle(
    request: KillSwitchToggleRequest, conn=Depends(get_conn)
) -> dict[str, Any]:
    row = _toggle_global(
        conn, active=request.active, reason=request.reason, decided_by=request.operator
    )
    return _state_body(row)


@router.get("/kill-switch/audit/last")
def get_kill_switch_audit_last(conn=Depends(get_conn)) -> dict[str, Any]:
    row = _get_global_state(conn)
    if row is None or row["decided_by"] is None:
        raise HTTPException(
            status_code=404, detail="the global kill switch has never been toggled"
        )
    return _audit_body(row)
