"""Gatekeeper — EXTERNAL approval-action surface (ca-gatekeeper-approval).

This is the ONLY governance app with external ingress. It exposes two
functional routes, both Action.OpenUrl deep links rendered into a Teams
Adaptive Card and clicked by a human: GET /approval-action/{link_token}
(the older gate_decisions flow) and GET /decide (Appendix D PR 3's
option_cards / approval_decisions flow, app/routers/option_decide.py).
It is protected by Container Apps built-in authentication (Entra ID
identity provider, unauthenticatedClientAction=Return401), so the
platform terminates the sign-in and injects X-MS-CLIENT-PRINCIPAL-*
headers that app/auth.py turns into the recorded approver/decider for
both routes.

The internal APIs (/gate-check, /decisions/{id}) are deliberately NOT
mounted here — they live on main.py / ca-gatekeeper with
ingress external:false. Two separate app objects means route separation
cannot be lost to a misconfigured flag.
"""

from __future__ import annotations

from app.routers import approval_action, option_decide
from fastapi import FastAPI

app = FastAPI(
    title="CMOS Gatekeeper (approval action)",
    description="Entra-ID-protected approve/reject deep-link handler.",
    version="0.1.0",
)

app.include_router(approval_action.router)
app.include_router(option_decide.router)


@app.get("/healthz", tags=["ops"])
def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "gatekeeper-approval"}
