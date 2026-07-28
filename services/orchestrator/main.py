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
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from orchestrator import config, db, worker
from orchestrator.logging_config import get_logger, log_event
from orchestrator.loop_loader import load_loop
from orchestrator.servicebus import producer
from orchestrator.servicebus.consumer import ServiceBusConsumer, build_client

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
    """Static liveness/readiness probe."""
    return {"status": "ok"}


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
        log_event(logger, logging.WARNING, "status_db_unavailable", error=str(exc))
        tasks = []
    return JSONResponse(content=tasks)
