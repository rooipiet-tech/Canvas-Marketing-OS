"""Vault service — FastAPI app.

/health is dependency-free (no DB round-trip) per SCOPE convention shared
with services/model-gateway/main.py — it must return 2xx even before the
database is reachable, so Container Apps ingress/startup probes and agent
smoke-testing never block on DB connectivity (AC-017).
"""

from __future__ import annotations

from fastapi import FastAPI

from .models import OBJECT_TYPES
from .routers.consent import router as consent_router
from .routers.objects import build_assets_router, build_object_router
from .routers.retention import router as retention_router
from .routers.utilisation import router as utilisation_router

app = FastAPI(title="vault")


@app.get("/health")
def health() -> dict:
    """Static liveness/readiness probe — no DB access."""
    return {"status": "ok"}


for _object_type, _config in OBJECT_TYPES.items():
    if _object_type == "assets":
        app.include_router(
            build_assets_router(_config), prefix=f"/{_config.path}", tags=[_object_type]
        )
    else:
        app.include_router(
            build_object_router(_config), prefix=f"/{_config.path}", tags=[_object_type]
        )

app.include_router(consent_router, tags=["consent"])
app.include_router(retention_router, tags=["retention"])
app.include_router(utilisation_router, tags=["utilisation"])
