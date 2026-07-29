"""The ONLY mutating route in the entire console app (CONSOLE-005).

POST /kill-switch/toggle — requires an authenticated Easy-Auth principal
(401 without one); calls services.toggle_kill_switch with the operator
identity read from Easy Auth headers (GOAL-004, AGENT-002).

Accepts BOTH request bodies:

- `application/json` — the documented programmatic-client contract
  (console/README.md, AGENT-002): `{"active": bool, "reason": str}`.
- `application/x-www-form-urlencoded` — what a real browser actually
  sends when it submits console/app/templates/kill_switch.html's plain
  HTML `<form method="post">` (no `enctype` on that form means the
  browser default, form-urlencoded, not JSON — a real browser submission
  against a JSON-only route 422s; see CONSOLE-005/GOAL-004/AGENT-002/
  POLISH-001).

`_parse_toggle_body` picks the parser by Content-Type so both shapes
funnel into the same `KillSwitchToggleBody` and the same handler body —
no route/behavior duplication.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, ValidationError

from app.app_instance import app
from app.auth import principal_from_headers
from app.clients import GatekeeperClient, get_gatekeeper_client
from app.services import toggle_kill_switch

_FORM_ENCODED_CONTENT_TYPES = (
    "application/x-www-form-urlencoded",
    "multipart/form-data",
)

_TRUTHY_FORM_VALUES = {"true", "1", "yes", "on"}


class KillSwitchToggleBody(BaseModel):
    active: bool
    reason: str


async def _parse_toggle_body(request: Request) -> KillSwitchToggleBody:
    content_type = request.headers.get("content-type", "")

    if any(marker in content_type for marker in _FORM_ENCODED_CONTENT_TYPES):
        form = await request.form()
        raw_active = form.get("active")
        raw_reason = form.get("reason")
        if raw_active is None or raw_reason is None:
            raise HTTPException(
                status_code=422, detail="form body must include 'active' and 'reason'"
            )
        active = str(raw_active).strip().lower() in _TRUTHY_FORM_VALUES
        try:
            return KillSwitchToggleBody(active=active, reason=str(raw_reason))
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors()) from exc

    # Default: application/json (also what an unset/unknown Content-Type
    # falls through to, matching FastAPI's own prior JSON-only behavior).
    try:
        payload = await request.json()
    except Exception as exc:  # noqa: BLE001 - surfaced as a 422, not a 500
        raise HTTPException(status_code=422, detail="invalid JSON body") from exc
    try:
        return KillSwitchToggleBody(**payload)
    except (TypeError, ValidationError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/kill-switch/toggle")
async def kill_switch_toggle(
    request: Request,
    body: KillSwitchToggleBody = Depends(_parse_toggle_body),
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
