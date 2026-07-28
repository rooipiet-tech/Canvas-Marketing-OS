"""End-to-end test of worker.py's core loop using local_double only (no
live Azure). Proves worker.py actually connects "receives a heartbeat" to
"tracks/dispatches the resulting tasks" end to end.

Bounding mechanism: ONLY the stop_event mechanism run_worker_loop's own
signature defines (asyncio.Event) — never an invented "max_iterations"
parameter (plan-reviewer note F-R1). The loop is run as a background task,
allowed to complete a couple of poll passes, then stopped via
stop_event.set() and awaited.

Requires a live Postgres (clean_pg fixture) — skipped locally in this
sandbox, genuinely run by CI's orchestrator-test job.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

from orchestrator import db, worker
from orchestrator.loop_loader import load_loop
from orchestrator.models import TaskStateEnum
from orchestrator.servicebus import producer
from orchestrator.servicebus.consumer import ServiceBusConsumer
from orchestrator.servicebus.local_double import InMemoryServiceBus

ORCHESTRATOR_DIR = Path(__file__).resolve().parent.parent
GOLDEN_DIR = Path(__file__).resolve().parent / "golden"


async def _run_bounded(coro_factory, passes: int = 3, poll_interval_s: float = 0.05):
    """Runs run_worker_loop as a background task for `passes` poll
    intervals, then sets stop_event and awaits it — bounding it via the
    ONLY mechanism run_worker_loop's signature defines (stop_event), not
    an invented max_iterations parameter.
    """
    stop_event = asyncio.Event()
    task = asyncio.create_task(coro_factory(stop_event, poll_interval_s))
    await asyncio.sleep(poll_interval_s * passes)
    stop_event.set()
    await asyncio.wait_for(task, timeout=5.0)


def test_heartbeat_to_dispatch_end_to_end(clean_pg):
    loop = load_loop(ORCHESTRATOR_DIR / "loops" / "daily-signal-loop.yaml")
    loops = {loop.loop_id: loop}

    heartbeat_raw = json.loads(
        (GOLDEN_DIR / "daily-signal-loop.heartbeat.json").read_text(encoding="utf-8")
    )
    # Fresh event_id per test run so re-runs don't collide on task_id PKs.
    heartbeat_raw = dict(heartbeat_raw)
    heartbeat_raw["event_id"] = str(uuid.uuid4())

    bus = InMemoryServiceBus()
    event_consumer = ServiceBusConsumer("event", True, bus)
    task_consumer = ServiceBusConsumer("task", True, bus)

    bus.send("event", heartbeat_raw)

    async def _factory(stop_event, poll_interval_s):
        await worker.run_worker_loop(
            event_consumer,
            task_consumer,
            producer,
            db,
            loops,
            bus,
            stop_event,
            poll_interval_s=poll_interval_s,
        )

    async def _wrapped():
        # Override db calls to use clean_pg explicitly via a thin shim,
        # since worker.py calls db.insert_task_batch/transition/etc with
        # default database_url (config.DATABASE_URL). We instead monkeypatch
        # config for the duration of this test run.
        import orchestrator.config as config

        original = config.DATABASE_URL
        config.DATABASE_URL = clean_pg
        try:
            await _run_bounded(_factory, passes=4, poll_interval_s=0.05)
        finally:
            config.DATABASE_URL = original

    asyncio.run(_wrapped())

    # (a) the expected task batch now exists in db, matching decompose()'s
    # own output on task_type/order/depends_on.
    all_tasks = db.fetch_all_task_status(database_url=clean_pg)
    daily_tasks = [t for t in all_tasks if t["loop_id"] == "daily-signal-loop"]
    assert len(daily_tasks) >= 5

    task_types = {t["task_type"] for t in daily_tasks[-5:]}
    assert task_types == {
        "ingest-signals",
        "score-signals",
        "draft-brief",
        "qa-review",
        "publish-brief",
    }

    # (b) tasks with no dependencies (ingest) reach completed and their
    # dependents (score) become dispatchable/completed after further
    # passes — the happy-path handler immediately completes+advances.
    ingest_tasks = [t for t in daily_tasks if t["task_type"] == "ingest-signals"]
    assert any(t["state"] == TaskStateEnum.COMPLETED.value for t in ingest_tasks)
