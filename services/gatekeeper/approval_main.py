"""Gatekeeper — EXTERNAL approval-action surface (ca-gatekeeper-approval).

This is the ONLY governance app with external ingress, and it exposes
exactly one functional route: GET /approval-action/{link_token}. It is
protected by Container Apps built-in authentication (Entra ID identity
provider, unauthenticatedClientAction=Return401), so the platform
terminates the sign-in and injects X-MS-CLIENT-PRINCIPAL-* headers that
app/auth.py turns into the recorded approver.

The internal APIs (/gate-check, /decisions/{id}) are deliberately NOT
mounted here — they live on main.py / ca-gatekeeper with
ingress external:false. Two separate app objects means route separation
cannot be lost to a misconfigured flag.
"""

from __future__ import annotations

from app.routers import approval_action
from fastapi import FastAPI

app = FastAPI(
    title="CMOS Gatekeeper (approval action)",
    description="Entra-ID-protected approve/reject deep-link handler.",
    version="0.1.0",
)

app.include_router(approval_action.router)


@app.get("/healthz", tags=["ops"])
def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "gatekeeper-approval"}
