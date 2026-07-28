"""Model Gateway service — FastAPI app (router only).

Implements exactly the two operations in the frozen contract
(contracts/model-gateway/openapi.yaml): POST /v1/completions and
GET /v1/health.

Deliberately thin: the request body is passed through as a permissive dict,
so additive optional fields (task_ref, deliberate) reach the orchestrator
untouched and no hand-duplicated copy of the frozen schema exists here. All
gateway behaviour — routing, redaction, budgets, caching, metering — lives
in completion.py.
"""

from __future__ import annotations

import completion
import db
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI(title="model-gateway", version="1.0.0")


@app.post("/v1/completions")
async def create_completion(
    request: Request,
    repo=Depends(db.get_repository),
) -> JSONResponse:
    """Create a model completion (see the frozen OpenAPI contract)."""
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001 - malformed body is a client error
        payload = None
    if not isinstance(payload, dict):
        return JSONResponse(
            status_code=400,
            content={"error": {"code": "INVALID_REQUEST", "message": "body must be a JSON object"}},
        )

    status_code, body = await completion.handle_completion(payload, repo)
    return JSONResponse(status_code=status_code, content=body)


@app.get("/v1/health")
async def health() -> dict:
    """Liveness/readiness probe."""
    return {"status": "ok"}
