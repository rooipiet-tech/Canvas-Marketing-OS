"""Vault service — FastAPI app.

/health is dependency-free (no DB round-trip) per SCOPE convention shared
with services/model-gateway/main.py — it must return 2xx even before the
database is reachable, so Container Apps ingress/startup probes and agent
smoke-testing never block on DB connectivity (AC-017).
"""

from __future__ import annotations

import json
import logging
import time
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from .models import OBJECT_TYPES
from .routers.consent import router as consent_router
from .routers.objects import build_assets_router, build_object_router
from .routers.retention import router as retention_router
from .routers.utilisation import router as utilisation_router

app = FastAPI(title="vault")

# Structured request-log stream, separate from vault_internal.audit_log
# (which only covers the 3 audit-emitting rejection/deletion paths — see
# vault/audit.py). This middleware emits one JSON line per HTTP request
# — including ordinary successful CRUD calls, which previously produced
# only unstructured uvicorn access-log text — so an agent gets a
# queryable structured trace for every operation, not just rejections
# (AC-009 agent-native finding).
_request_logger = logging.getLogger("vault.requests")
if not _request_logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(message)s"))
    _request_logger.addHandler(_handler)
    _request_logger.setLevel(logging.INFO)
    _request_logger.propagate = False

# URL path segment -> object_table name, derived from the same registry
# that drives routing (vault/models.py), so this stays in sync with the 9
# object types without hand-maintained duplication.
_PATH_SEGMENT_TO_TABLE = {config.path: config.name for config in OBJECT_TYPES.values()}


@app.middleware("http")
async def structured_request_logging(request: Request, call_next):  # noqa: ANN001, ANN201
    correlation_id = str(uuid.uuid4())
    path_parts = [p for p in request.url.path.split("/") if p]
    object_table = _PATH_SEGMENT_TO_TABLE.get(path_parts[0]) if path_parts else None
    object_id = path_parts[1] if object_table and len(path_parts) > 1 else None

    start = time.monotonic()
    response = await call_next(request)
    duration_ms = round((time.monotonic() - start) * 1000, 2)

    record = {
        "correlation_id": correlation_id,
        "method": request.method,
        "path": request.url.path,
        "status": response.status_code,
        "object_table": object_table,
        "object_id": object_id,
        "duration_ms": duration_ms,
    }
    _request_logger.info(json.dumps(record, default=str))
    response.headers["X-Correlation-Id"] = correlation_id
    return response


@app.exception_handler(HTTPException)
async def _http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Every 4xx this service raises deliberately (vault/routers/objects.py's
    `_error()` helper, and the one `raise HTTPException(...)` call site in
    vault/routers/retention.py) already builds `exc.detail` as the exact
    `{"error": {"message": ..., "code": ..., "field": ...}}` body
    contracts/vault-api.yaml's Error schema (and AC-002's taxonomy
    enforcement) requires.

    FastAPI's own default HTTPException handler
    (fastapi.exception_handlers.http_exception_handler) unconditionally
    re-wraps whatever `exc.detail` is under a *further* top-level "detail"
    key: `JSONResponse({"detail": exc.detail}, ...)`. Since our exc.detail
    was already `{"error": {...}}`, the response actually sent to callers
    was `{"detail": {"error": {...}}}` -- no top-level "error" key at all,
    for every single taxonomy/consent/not-found rejection this service
    ever returns. That's a real gap between the documented contract and
    live behaviour, not a test-only mismatch: any consumer (including this
    service's own smoke tests) reading `body["error"]["field"]` per the
    contract got a KeyError.

    This handler ships `exc.detail` as the response body as-is whenever it
    already matches the Error contract shape (a dict with an "error" key),
    and only falls back to FastAPI's default `{"detail": ...}` envelope for
    the plain-string-detail HTTPExceptions Starlette/FastAPI may raise
    internally (e.g. 404 for an unmatched route, 405 method not allowed),
    which the contract does not attempt to describe.
    """
    headers = getattr(exc, "headers", None)
    if isinstance(exc.detail, dict) and "error" in exc.detail:
        return JSONResponse(status_code=exc.status_code, content=exc.detail, headers=headers)
    content = {"detail": exc.detail}
    return JSONResponse(status_code=exc.status_code, content=content, headers=headers)


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
