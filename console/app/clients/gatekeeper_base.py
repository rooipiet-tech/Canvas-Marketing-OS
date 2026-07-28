"""GatekeeperClient Protocol — approval inbox (read-only) + kill-switch
(read + the console's one write action).

Mirrors, structurally, session/s4-governance's uncommitted Gatekeeper
service shapes (governance.approval_inbox, governance.kill_switches;
observed read-only this session at C:\\Users\\rooip\\cmos-s4\\services\\
gatekeeper\\app\\{approval_inbox,kill_switch,auth}.py) — no real Gatekeeper
flag-write HTTP endpoint exists anywhere yet (confirmed in research), so
this session ships against a documented mock (see gatekeeper_mock.py) with
a config-only cutover to gatekeeper_real.py once session/s4-governance
merges (INTEG-002).
"""

from __future__ import annotations

from typing import Any, Protocol


class GatekeeperClient(Protocol):
    async def list_approval_inbox(self) -> list[dict[str, Any]]: ...

    async def get_kill_switch_state(self) -> dict[str, Any]: ...

    async def toggle_kill_switch(
        self, active: bool, reason: str, operator: str
    ) -> dict[str, Any]: ...

    async def get_last_audit_entry(self) -> dict[str, Any] | None: ...
