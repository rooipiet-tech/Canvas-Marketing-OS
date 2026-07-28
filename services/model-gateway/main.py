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
# container.
#
# Scoped to the `model-gateway` logger ALONE, never to the root logger. A
# root-level logging.basicConfig() would switch on INFO for every third-party
# library in the process too — most immediately httpx, which emits a
# plain-text `HTTP Request: POST ... "HTTP/1.1 200 OK"` line on every real
# provider call. Those would land in the same stream, in the same bare
# format, and destroy the one property this configuration exists to provide:
# every line on the stream is a parseable JSON document (AC-31). The root
# logger and every third-party logger are therefore left at Python's
# defaults, untouched — as are uvicorn's own loggers, which uvicorn
# configures itself (its dictConfig has no `root` key and sets
# disable_existing_loggers: False, so it neither reaches this logger nor is
# reached by it).
#
# `propagate = False` completes the isolation in the other direction: these
# records stop here and never flow up to the root logger, so nothing that
# later attaches a handler to root can double-emit or reformat them.
logger = logging.getLogger("model-gateway")
if not logger.handlers:
    # A bare `%(message)s`: the messages are already complete JSON documents,
    # and a `LEVEL:name:message` prefix would break line-level JSON parsing
    # for any agent scraping these logs.
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(_handler)
logger.setLevel(logging.INFO)
logger.propagate = False

app = FastAPI(title="model-gateway", version="1.0.0")


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

    The wire message is a fixed literal, never `str(exc)`. Exception text on
    this path is arbitrary internal detail — a psycopg connection string, a
    filesystem path, a config value, a fragment of a caller's own payload —
    and Starlette's own `debug=False` default was careful to say nothing at
    all. `_log_failure` above keeps the full type and message server-side, so
    an operator loses nothing; only the caller does.
    """
    _log_failure("INTERNAL_ERROR", exc)
    return _error_response(500, "INTERNAL_ERROR", "internal gateway error")


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
