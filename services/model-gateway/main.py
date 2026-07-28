"""Model Gateway service — FastAPI app (router only).

Implements exactly the two operations in the frozen contract
(contracts/model-gateway/openapi.yaml): POST /v1/completions and
GET /v1/health.

Deliberately thin: the request body is read as a plain dict and validated
against the frozen CompletionRequest JSON Schema inside completion.py, so no
hand-duplicated copy of the frozen schema exists here and additive optional
fields (task_ref, deliberate) still reach the orchestrator untouched. All
gateway behaviour — validation, routing, redaction, budgets, caching,
metering — lives in completion.py.

Two process-level concerns do live here, because nowhere else runs at import
time:

* logging configuration (see below), and
* exception handling, so that *every* failure path answers with the frozen
  Error schema rather than Starlette's plain-text default 500.
"""

from __future__ import annotations

import json
import logging

import completion
import db
import httpx
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

# Logging is configured HERE, at import time, before the app object exists —
# uvicorn's default logging config touches only the `uvicorn*` loggers and
# leaves the root logger handler-less at WARNING, which would make every
# `logger.info(json.dumps(...))` in completion.py a silent no-op in the
# container. The format is a bare `%(message)s` on purpose: the messages are
# already complete JSON documents, and basicConfig's default
# `LEVEL:name:message` prefix would break line-level JSON parsing for any
# agent scraping these logs.
logging.basicConfig(level=logging.INFO, format="%(message)s")

app = FastAPI(title="model-gateway", version="1.0.0")

logger = logging.getLogger("model-gateway")


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    """A response body matching the frozen Error schema."""
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )


def _log_failure(code: str, exc: BaseException, **extra) -> None:
    """One JSON line per failure — same parseable shape as completion.py's.

    Deliberately not logger.exception(): Starlette's ServerErrorMiddleware
    re-raises after this handler responds, so uvicorn already prints the full
    traceback. Emitting it twice would only add a second multi-line,
    non-JSON-parseable block to the stream a log-reading agent has to skip.
    """
    logger.error(
        json.dumps(
            {
                "event": "gateway_failure",
                "code": code,
                "error_type": type(exc).__name__,
                "message": str(exc),
                **extra,
            }
        )
    )


@app.exception_handler(httpx.HTTPStatusError)
async def upstream_provider_error(request: Request, exc: httpx.HTTPStatusError) -> JSONResponse:
    """An upstream provider answered non-2xx (provider adapter raise_for_status).

    Still a 500: from the caller's point of view the gateway failed, and the
    frozen contract documents 500 as "Upstream provider or gateway failure".
    The distinct code lets an agent tell an upstream outage apart from a
    gateway bug without parsing prose.
    """
    status = exc.response.status_code if exc.response is not None else "unknown"
    _log_failure("PROVIDER_ERROR", exc, upstream_status=status)
    return _error_response(
        500,
        "PROVIDER_ERROR",
        f"upstream provider returned HTTP {status}",
    )


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception) -> JSONResponse:
    """Any other unexpected failure (database error, bug) — never a bare 500.

    Starlette's default handler returns a plain-text body, which does not
    satisfy the frozen contract's 500 response schema.
    """
    _log_failure("INTERNAL_ERROR", exc)
    return _error_response(500, "INTERNAL_ERROR", str(exc) or "internal gateway error")


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
        return _error_response(400, "INVALID_REQUEST", "body must be a JSON object")

    status_code, body = await completion.handle_completion(payload, repo)
    return JSONResponse(status_code=status_code, content=body)


@app.get("/v1/health")
async def health() -> dict:
    """Liveness/readiness probe."""
    return {"status": "ok"}
