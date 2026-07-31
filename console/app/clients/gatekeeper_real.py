"""GatekeeperHttpClient — genuine httpx implementation of GatekeeperClient.

Talks to a REST wrapper around the real Gatekeeper's kill_switch.py/
approval_inbox.py that does not exist yet (INTEG-002) — session/
s4-governance HAS merged (2026-07-31), but its Gatekeeper only exposes
gate_check/decisions/approval_action over HTTP, none of which is a
list-approvals or read/toggle-kill-switch route; see console/README.md's
"Switching Gatekeeper from mock to real" for the full finding. Once that
wrapper is built and merged, switching GATEKEEPER_API_MODE from "mock" to
"real" (plus GATEKEEPER_API_BASE_URL) is the only change needed here —
every caller depends only on the GatekeeperClient Protocol.
"""

from __future__ import annotations

from typing import Any

import httpx


class GatekeeperHttpClient:
    def __init__(self, base_url: str, *, timeout: float = 10.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    async def list_approval_inbox(self) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout) as client:
            response = await client.get("/approval-inbox")
            response.raise_for_status()
            return response.json()

    async def get_kill_switch_state(self) -> dict[str, Any]:
        async with httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout) as client:
            response = await client.get("/kill-switch")
            response.raise_for_status()
            return response.json()

    async def toggle_kill_switch(
        self, active: bool, reason: str, operator: str
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout) as client:
            response = await client.post(
                "/kill-switch/toggle",
                json={"active": active, "reason": reason, "operator": operator},
            )
            response.raise_for_status()
            return response.json()

    async def get_last_audit_entry(self) -> dict[str, Any] | None:
        async with httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout) as client:
            response = await client.get("/kill-switch/audit/last")
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()
