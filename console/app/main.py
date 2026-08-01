"""Console entrypoint — `uvicorn app.main:app`.

Importing routes_reads / routes_write registers their route decorators
onto the shared app object from app_instance.py (no include_router
needed — see app_instance.py's docstring).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import Request

# Importing these modules is what registers every route on `app` (their
# decorators run at import time). Imported via `from app import ...`
# rather than `import app.routes_reads` because `app` is rebound below to
# the FastAPI instance, which would shadow the package name for any later
# `import app.xxx` statement in this module.
from app import routes_reads, routes_write  # noqa: F401
from app.app_instance import app
from app.clients import get_gatekeeper_client, get_vault_client
from app.seed import seed_from_env
from app.telemetry_wiring import close_request_span, configure_tracer, open_request_span


@asynccontextmanager
async def _lifespan(_app) -> AsyncIterator[None]:
    # Best-effort (AC-03/AC-04) -- a missing APPLICATIONINSIGHTS_
    # CONNECTION_STRING is the normal state absent a deployed App
    # Insights resource, and must never crash startup.
    try:
        configure_tracer()
    except Exception:  # noqa: BLE001 - telemetry must never crash startup
        pass
    seed_from_env(get_vault_client(), get_gatekeeper_client())
    yield


@app.middleware("http")
async def _telemetry_span(request: Request, call_next):  # noqa: ANN001, ANN201
    """AC-03/AC-04/DE-5: one span per HTTP request, joining the caller's
    trace if a W3C traceparent header is present. function_id is the
    request's own path (a real per-request concept for a read-mostly
    console); task_ref falls back to the path again when no more specific
    id is available at this generic layer."""
    span_cm, otel_token, _span = open_request_span(
        request.headers, function_id=request.url.path, task_ref=request.url.path
    )
    try:
        return await call_next(request)
    finally:
        close_request_span(span_cm, otel_token)


# `on_event("startup")` is deprecated in favor of the lifespan context
# manager (output-reviewer F3) — `app` is already constructed in
# app_instance.py, so the lifespan is attached to the router directly
# here rather than passed to the FastAPI() constructor.
app.router.lifespan_context = _lifespan


@app.get("/health")
def health() -> dict:
    """Unauthenticated liveness/readiness probe.

    Container Apps' native liveness-probe path is expected to bypass the
    Easy Auth ingress proxy (see infra/modules/console/console-app.bicep's
    probe configuration and docs/accepted-risks.md's residual-risk note);
    this route intentionally carries no auth check.
    """
    return {"status": "ok"}
