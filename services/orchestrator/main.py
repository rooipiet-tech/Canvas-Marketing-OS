"""Canvas Marketing OS — orchestrator service.

FastAPI app exposing GET /health and GET /status (AC-018, AC-019 — reads
synchronously from the orchestrator's own schema on every request, no
cache). The `lifespan` context manager spawns orchestrator.worker.run_worker_loop
as a long-lived background asyncio.Task on startup, and cancels/awaits it
cleanly on shutdown.

Both /status and lifespan startup wrap DB/Service-Bus construction in
try/except so a missing/unreachable DATABASE_URL or SERVICE_BUS_NAMESPACE
never crashes FastAPI startup — required because AC-018's literal verify
command runs `uvicorn main:app` with neither env var set, in which case the
worker loop simply runs against the local Service Bus double and /status
returns [].
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from orchestrator import config, db, worker
from orchestrator.logging_config import get_logger, log_event, sanitize_exception_text
from orchestrator.loop_loader import load_loop
from orchestrator.run_state import build_run_state
from orchestrator.servicebus import producer
from orchestrator.servicebus.consumer import ServiceBusConsumer, build_client
from orchestrator.task_review import build_task_review
from orchestrator.telemetry_wiring import configure_tracer

logger = get_logger("main")

LOOPS_DIR = Path(__file__).resolve().parent / "loops"


def _load_shipped_loops() -> dict[str, object]:
    loops = {}
    for loop_path in sorted(LOOPS_DIR.glob("*.yaml")):
        try:
            loop = load_loop(loop_path)
            loops[loop.loop_id] = loop
        except Exception as exc:  # noqa: BLE001 - startup must not crash on a bad loop file
            log_event(
                logger, logging.ERROR, "loop_load_failed", path=str(loop_path), error=str(exc)
            )
    return loops


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    stop_event = asyncio.Event()
    worker_task: asyncio.Task | None = None

    # Best-effort telemetry setup (AC-03/AC-04) -- a missing
    # APPLICATIONINSIGHTS_CONNECTION_STRING (this sandbox, most local dev)
    # is a completely normal state and must never crash startup, matching
    # every other config-driven degrade-gracefully path in this file.
    try:
        configure_tracer()
    except Exception as exc:  # noqa: BLE001 - telemetry must never crash startup
        log_event(logger, logging.WARNING, "telemetry_setup_failed", error=str(exc))

    try:
        use_local_double = not bool(config.SERVICE_BUS_NAMESPACE)
        client = build_client(
            use_local_double=use_local_double, namespace=config.SERVICE_BUS_NAMESPACE
        )
        event_consumer = ServiceBusConsumer("event", use_local_double, client)
        task_consumer = ServiceBusConsumer("task", use_local_double, client)
        loops = _load_shipped_loops()

        worker_task = asyncio.create_task(
            worker.run_worker_loop(
                event_consumer,
                task_consumer,
                producer,
                db,
                loops,
                client,
                stop_event,
                poll_interval_s=config.WORKER_POLL_INTERVAL_S,
            )
        )
        log_event(logger, logging.INFO, "worker_loop_started", use_local_double=use_local_double)
    except Exception as exc:  # noqa: BLE001 - startup must never crash on SB/DB unavailability
        log_event(logger, logging.WARNING, "worker_loop_start_failed", error=str(exc))
        worker_task = None

    # A2 (B5). The worker is a single asyncio.Task inside this process. If
    # the block above raised, worker_task is None, one WARNING is logged,
    # and /health still returns 200 -- the system stalls completely while
    # looking healthy. Publishing the handle here is what lets /readiness
    # tell the difference; it was previously a local that nothing outside
    # this function could see.
    app.state.worker_task = worker_task

    try:
        yield
    finally:
        stop_event.set()
        if worker_task is not None:
            try:
                await asyncio.wait_for(worker_task, timeout=5.0)
            except (asyncio.TimeoutError, Exception) as exc:  # noqa: BLE001
                log_event(logger, logging.WARNING, "worker_loop_shutdown_timeout", error=str(exc))


app = FastAPI(title="orchestrator", lifespan=lifespan)


@app.get("/health")
def health() -> dict:
    """Static LIVENESS probe -- deliberately dumb, deliberately unchanged.

    This answers only "is the process up". It must keep returning 200
    whenever the process can serve a request, because that is what a
    container liveness probe is for: a probe that goes red on a
    dependency outage gets the container killed and restarted, which
    fixes nothing and loses the in-flight work.

    Readiness -- can this process actually do its job -- is GET
    /readiness. See A2/B5 there for why the two must not be the same
    endpoint.
    """
    return {"status": "ok"}


# --- readiness (A2: F6, B5, O1) ----------------------------------------
#
# WHAT THIS CLOSES. /health returned 200 unconditionally and was the only
# health surface, so three separate failures were invisible:
#
#   B5  the worker is a single asyncio.Task inside this FastAPI process.
#       If its startup raised, worker_task is None, `worker_loop_start_
#       failed` is logged at WARNING, and /health still returns 200. The
#       system stalls completely while looking healthy.
#   O1  a missing TEAMS_WEBHOOK_URL, DATABASE_URL or App Insights
#       connection string each log and continue, so config-ABSENT is
#       indistinguishable from config-BROKEN.
#   F6  dead-lettered tasks emit a DeadLetterAlert nothing consumes.
#       (The alerting half of F6 is in Bicep, not here.)
#
# THE EXPECTATION VARS. Absence cannot be an error by default: every one
# of these integrations is legitimately absent in local dev and in CI,
# and making them hard requirements would break both. So the deployment
# declares what it expects -- CMOS_EXPECT_TEAMS=true in cmos-dev says "a
# Teams webhook is supposed to be configured here", and only then does
# its absence make this endpoint red. Unset means "not expected", which
# is exactly today's behaviour and why this is safe to add.
_EXPECTATIONS: tuple[tuple[str, str, str], ...] = (
    # (expectation env var, human name, the config attribute it requires)
    ("CMOS_EXPECT_DATABASE", "database", "DATABASE_URL"),
    ("CMOS_EXPECT_SERVICE_BUS", "service_bus", "SERVICE_BUS_NAMESPACE"),
    ("CMOS_EXPECT_APP_INSIGHTS", "app_insights", "APPLICATIONINSIGHTS_CONNECTION_STRING"),
    ("CMOS_EXPECT_TEAMS", "teams", "TEAMS_WEBHOOK_URL"),
    ("CMOS_EXPECT_VAULT", "vault", "VAULT_API_URL"),
)


def _expects(expectation_var: str) -> bool:
    return os.environ.get(expectation_var, "").strip().lower() in ("1", "true", "yes")


def _configured(attribute: str) -> bool:
    """Whether an integration's config is present RIGHT NOW.

    Read from the environment rather than from config.py's module-level
    constants: those are bound at import time, so a test (or a restart-
    free config change) that sets the variable afterwards would be
    invisible to them.
    """
    return bool((os.environ.get(attribute) or "").strip())


def _worker_state(worker_task: object | None) -> str:
    if worker_task is None:
        # Startup raised, or the app was constructed without a lifespan.
        return "not_started"
    done = getattr(worker_task, "done", None)
    if callable(done) and done():
        # The loop exited on its own. For a task that is supposed to run
        # for the life of the process, that is a stall, not a success --
        # so it is reported the same either way.
        return "stopped"
    return "running"


def _database_state() -> tuple[str, str | None]:
    if not _configured("DATABASE_URL"):
        return "not_configured", None
    try:
        db.fetch_all_task_status()
    except Exception as exc:  # noqa: BLE001 - the point is to report, not raise
        return "unreachable", sanitize_exception_text(exc)
    return "reachable", None


@app.get("/readiness")
def readiness() -> JSONResponse:
    """Can this process actually do its job? 200 when yes, 503 when no.

    Distinct from /health on purpose (see B5 above). A 503 here says
    "stop sending me work and page someone"; it must never be wired to a
    liveness probe, because restarting the container does not fix a
    missing webhook or an unreachable database.
    """
    checks: dict[str, object] = {}
    failures: list[str] = []

    worker_state = _worker_state(getattr(app.state, "worker_task", None))
    checks["worker"] = worker_state
    if worker_state != "running":
        failures.append(f"worker is {worker_state}")

    database_state, database_error = _database_state()
    checks["database"] = database_state
    if database_state == "unreachable":
        failures.append(f"database unreachable: {database_error}")
    # "not_configured" is only a failure when the deployment says it
    # expects one -- handled uniformly below with every other integration.

    integrations: dict[str, str] = {}
    for expectation_var, name, attribute in _EXPECTATIONS:
        expected = _expects(expectation_var)
        present = _configured(attribute)
        if not expected:
            integrations[name] = "configured" if present else "not_expected"
            continue
        integrations[name] = "configured" if present else "expected_but_absent"
        if not present:
            failures.append(f"{name} is expected ({expectation_var}) but {attribute} is unset")
    checks["integrations"] = integrations

    ready = not failures
    if not ready:
        log_event(logger, logging.ERROR, "readiness_failed", failures=failures)

    return JSONResponse(
        status_code=200 if ready else 503,
        content={"ready": ready, "checks": checks, "failures": failures},
    )


@app.get("/status")
def status() -> JSONResponse:
    """Machine-readable JSON listing every in-flight task with its full
    state history (AC-018). Reads synchronously from orchestrator.db on
    every request — no cache, per C13/AC-019. Degrades to [] when
    DATABASE_URL is not configured/reachable, rather than raising.
    """
    try:
        tasks = db.fetch_all_task_status()
    except Exception as exc:  # noqa: BLE001
        log_event(
            logger, logging.WARNING, "status_db_unavailable", error=sanitize_exception_text(exc)
        )
        tasks = []
    return JSONResponse(content=tasks)


@app.get("/runs/{task_ref}")
def get_run_state(task_ref: str) -> JSONResponse:
    """AGENT-NATIVE run-state read (AC-15): a plain script (no browser, no
    interactive Entra session) queries every stage's task status for a
    run, span presence (best-effort), and the REAL gatekeeper
    approval_inbox decision status for the run's request-approval stage
    -- distinct from that task's own always-COMPLETED-once-issued state.
    """
    try:
        result = build_run_state(task_ref, db)
    except Exception as exc:  # noqa: BLE001 - a DB/Gatekeeper outage must not 500 this endpoint
        log_event(
            logger, logging.WARNING, "run_state_lookup_failed", error=sanitize_exception_text(exc)
        )
        return JSONResponse(status_code=503, content={"error": "run-state lookup failed"})
    if result is None:
        return JSONResponse(status_code=404, content={"error": f"unknown task_ref: {task_ref}"})
    return JSONResponse(content=result)


@app.get("/tasks/{task_id}/review")
def get_task_review(task_id: str) -> JSONResponse:
    """F-TEAMS-CARD-REVIEW-LINK (11 Aug 2026): human-review detail for one
    task -- what the console's GET /review/{task_id} page calls to render
    the full QA violations and draft text a Teams "needs edit" card only
    carries a 280-char excerpt of. Internal-ingress-only, same trust
    boundary as /health, /status and /runs/{task_ref} above (this
    Container App's ingress is internal-only; console is the only caller)
    -- no additional auth check, consistent with every other route here.
    See orchestrator/task_review.py for the actual lookup + Vault fetch.
    """
    try:
        result = build_task_review(task_id, db)
    except Exception as exc:  # noqa: BLE001 - a DB/Vault outage must not 500 this endpoint
        log_event(
            logger, logging.WARNING, "task_review_lookup_failed", error=sanitize_exception_text(exc)
        )
        return JSONResponse(status_code=503, content={"error": "task review lookup failed"})
    if result is None:
        return JSONResponse(status_code=404, content={"error": f"unknown task_id: {task_id}"})
    return JSONResponse(content=result)
