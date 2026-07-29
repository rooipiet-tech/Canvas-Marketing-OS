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

from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ValidationError

from app.app_instance import app
from app.auth import principal_from_headers
from app.clients import GatekeeperClient, get_gatekeeper_client
from app.routes_reads import require_principal
from app.services import toggle_kill_switch

_FORM_ENCODED_CONTENT_TYPES = (
    "application/x-www-form-urlencoded",
    "multipart/form-data",
)

_TRUTHY_FORM_VALUES = {"true", "1", "yes", "on"}


class KillSwitchToggleBody(BaseModel):
    active: bool
    reason: str


def _same_origin_or_reject(request: Request) -> None:
    """CSRF defense-in-depth for the form-encoded (browser) path.

    A plain `<form method="post">` with no CSRF token is the classic CSRF
    shape: a malicious third-party page can trigger this exact
    cross-origin form submission and the browser will still forward the
    Easy-Auth session cookie. Easy Auth's own SameSite cookie attribute is
    an external, unverified-in-code guarantee (this app has no live
    deployment to inspect it against) — so, matching this repo's own
    RISK-003 precedent of never relying solely on infra-layer guarantees,
    this checks Origin (falling back to Referer) against the request's
    own host and rejects on mismatch or absence. The JSON API path
    (AGENT-002) is unaffected: a plain cross-origin HTML form cannot set
    Content-Type: application/json without a CORS preflight, so it never
    reaches this check.
    """
    origin = request.headers.get("origin") or request.headers.get("referer")
    if origin is None:
        raise HTTPException(status_code=403, detail="missing Origin/Referer header")
    origin_host = urlsplit(origin).netloc
    if origin_host != request.url.netloc:
        raise HTTPException(status_code=403, detail="cross-origin form submission rejected")


async def _parse_toggle_body(request: Request) -> KillSwitchToggleBody:
    content_type = request.headers.get("content-type", "")

    if any(marker in content_type for marker in _FORM_ENCODED_CONTENT_TYPES):
        _same_origin_or_reject(request)
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
    _principal: None = Depends(require_principal),
    body: KillSwitchToggleBody = Depends(_parse_toggle_body),
    gatekeeper_client: GatekeeperClient = Depends(get_gatekeeper_client),
):
    # require_principal (a sibling Depends, declared first so FastAPI
    # resolves it before _parse_toggle_body) already rejected an
    # unauthenticated caller with 401 — re-reading the principal here is
    # only to get its identity for the audit trail, not to re-check auth.
    principal = principal_from_headers(request.headers)

    state = await toggle_kill_switch(
        gatekeeper_client,
        active=body.active,
        reason=body.reason,
        operator=principal.decided_by,
    )

    # POLISH-006: a real browser's no-JS form submission (POST) should end
    # on the styled /kill-switch screen (GET), not a bare JSON response —
    # the standard POST-redirect-GET pattern. Only redirect for the form
    # path; programmatic JSON callers (AGENT-002) still get the JSON body
    # directly, unchanged.
    content_type = request.headers.get("content-type", "")
    if any(marker in content_type for marker in _FORM_ENCODED_CONTENT_TYPES):
        return RedirectResponse(url="/kill-switch", status_code=303)

    return state.model_dump()
