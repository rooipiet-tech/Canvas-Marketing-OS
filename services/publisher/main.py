"""Publisher — INTERNAL API (ca-publisher).

Reachable only from inside the VNet (ingress external:false, see
infra/modules/governance/publisher-app.bicep). Publisher has NO external
surface at all: the single externally reachable governance route in this
session is the Gatekeeper approval-action app.

Agent-native by construction (AC-17): POST /publish then
GET /publish-attempts/{id} — an internal agent caller can observe every
outcome, including every refusal reason, over plain HTTP.
"""

from __future__ import annotations

from app.routers import publish, publish_attempts
from fastapi import FastAPI

app = FastAPI(
    title="CMOS Publisher (internal)",
    description="Gate-token verification, refusal audit and Vault publish recording.",
    version="0.1.0",
)

app.include_router(publish.router)
app.include_router(publish_attempts.router)


@app.get("/healthz", tags=["ops"])
def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "publisher-internal"}
