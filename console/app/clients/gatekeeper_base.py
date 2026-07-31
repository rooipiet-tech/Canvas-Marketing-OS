"""GatekeeperClient Protocol — approval inbox (read-only) + kill-switch
(read + the console's one write action).

Mirrors, structurally, the now-merged (main, 2026-07-31) Gatekeeper's
governance.approval_inbox / governance.kill_switches Postgres tables
(infra/modules/governance/migrations/0001_governance_init.sql). Re-verified
at rebase time: still no real Gatekeeper HTTP endpoint for listing
approvals or reading/toggling the kill switch exists anywhere — main.py
only mounts gate_check/decisions, and approval_main.py only mounts
approval_action (the single-use token click handler); kill_switch.py's
is_blocked() and approval_inbox.py's helpers are called internally only.
This session ships against a documented mock (see gatekeeper_mock.py); the
cutover to gatekeeper_real.py (INTEG-002) is config-only ONLY once a REST
wrapper around those two modules is built and merged — see
console/README.md's "Switching Gatekeeper from mock to real" section for
the full finding.
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
