"""Gatekeeper — INTERNAL API (ca-gatekeeper).

Reachable only from inside the VNet (ingress external:false, see
infra/modules/governance/gatekeeper-app.bicep). This app deliberately does
NOT mount the approval-action router: the approve/reject click surface is
a physically separate app (approval_main.py, ca-gatekeeper-approval) so
that the only externally reachable governance route is the Entra-ID-
protected one. Route separation is enforced by which app object each
router is attached to, not by a runtime flag that could be misconfigured.

Agent-native by construction (AC-17): everything a human can observe here,
an internal agent caller can observe over plain HTTP — POST /gate-check
then GET /decisions/{id}, no Teams-only step in the loop.
"""

from __future__ import annotations

from app.routers import decisions, gate_check
from fastapi import FastAPI

app = FastAPI(
    title="CMOS Gatekeeper (internal)",
    description="Autonomy policy evaluation, gate decisions and gate-token issuance.",
    version="0.1.0",
)

app.include_router(gate_check.router)
app.include_router(decisions.router)


@app.get("/healthz", tags=["ops"])
def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "gatekeeper-internal"}
