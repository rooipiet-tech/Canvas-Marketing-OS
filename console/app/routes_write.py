"""The ONLY mutating route in the entire console app (CONSOLE-005).

POST /kill-switch/toggle — requires an authenticated Easy-Auth principal
(401 without one); calls services.toggle_kill_switch with the operator
identity read from Easy Auth headers (GOAL-004, AGENT-002).
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel

from app.app_instance import app
from app.auth import principal_from_headers
from app.clients import GatekeeperClient, get_gatekeeper_client
from app.services import toggle_kill_switch


class KillSwitchToggleBody(BaseModel):
    active: bool
    reason: str


@app.post("/kill-switch/toggle")
async def kill_switch_toggle(
    body: KillSwitchToggleBody,
    request: Request,
    gatekeeper_client: GatekeeperClient = Depends(get_gatekeeper_client),
):
    principal = principal_from_headers(request.headers)
    if principal is None:
        raise HTTPException(status_code=401, detail="authentication required")

    state = await toggle_kill_switch(
        gatekeeper_client,
        active=body.active,
        reason=body.reason,
        operator=principal.decided_by,
    )
    return state.model_dump()
