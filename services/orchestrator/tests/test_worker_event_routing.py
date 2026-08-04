"""Unit + local-double tests for worker.py's event-queue message-kind
discriminator (F-EVENTQ-DISCRIMINATE, 4 Aug 2026, heartbeat round 14).

Before this fix, run_worker_loop's event-queue handling force-validated
EVERY message pulled off the `event` queue as a HeartbeatEvent -- but
dead_letter.py's emit_alert (AC-012/AC-013) also publishes DeadLetterAlert
messages onto that same queue, by design. Every dead-letter therefore
crashed HeartbeatEvent.model_validate with 8 Pydantic errors (caught by
run_worker_loop's own try/except, non-fatal, but pure log noise on every
single dead-letter event -- confirmed reproducible in heartbeat rounds 13
and 14 via Azure Log Analytics).

No live Postgres is needed for either test here: _event_message_kind is a
pure function, and the DeadLetterAlert path through run_worker_loop never
touches db (only handle_heartbeat_message and handle_task_message do,
neither of which this test's queue traffic reaches).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from orchestrator import worker
from orchestrator.servicebus import producer
from orchestrator.servicebus.consumer import ServiceBusConsumer
from orchestrator.servicebus.local_double import InMemoryServiceBus


def _heartbeat_body() -> dict:
    return {
        "envelope_version": "1",
        "event_type": "heartbeat",
        "event_id": str(uuid.uuid4()),
        "loop_id": "daily-signal-loop",
        "fired_at": datetime.now(timezone.utc).isoformat(),
        "source": "test",
    }


def _dead_letter_alert_body() -> dict:
    return {
        "alert_version": "1",
        "task_id": str(uuid.uuid4()),
        "task_type": "ingest-signals",
        "loop_id": "daily-signal-loop",
        "failure_count": 3,
        "dead_lettered_at": datetime.now(timezone.utc).isoformat(),
    }


def test_event_message_kind_discriminates_heartbeat_alert_and_unknown():
    assert worker._event_message_kind(_heartbeat_body()) == "heartbeat"
    assert worker._event_message_kind(_dead_letter_alert_body()) == "dead_letter_alert"
    assert worker._event_message_kind({"something": "else"}) == "unknown"
    assert worker._event_message_kind("not-a-dict") == "unknown"
    assert worker._event_message_kind(None) == "unknown"


def test_worker_loop_acknowledges_dead_letter_alert_without_crashing():
    """A DeadLetterAlert on the `event` queue must be consumed cleanly --
    no HeartbeatEvent validation crash, no message left stuck in-flight
    or endlessly redelivered."""
    bus = InMemoryServiceBus()
    event_consumer = ServiceBusConsumer("event", True, bus)
    task_consumer = ServiceBusConsumer("task", True, bus)
    bus.send("event", _dead_letter_alert_body())

    async def _run():
        stop_event = asyncio.Event()
        task = asyncio.create_task(
            worker.run_worker_loop(
                event_consumer,
                task_consumer,
                producer,
                db=None,
                loops={},
                client=bus,
                stop_event=stop_event,
                poll_interval_s=0.05,
            )
        )
        await asyncio.sleep(0.15)
        stop_event.set()
        await asyncio.wait_for(task, timeout=5.0)

    asyncio.run(_run())

    # Queue is fully drained (message completed, not stuck in-flight or
    # redelivered) -- receiving again returns nothing.
    assert bus.receive("event", max_count=10) == []


def test_worker_loop_still_processes_real_heartbeats_after_discriminator(monkeypatch):
    """The discriminator must not regress the existing, already-proven
    heartbeat path -- a genuine heartbeat still reaches
    handle_heartbeat_message exactly as before."""
    calls: list[dict] = []

    async def _fake_handle_heartbeat_message(body, loops, db, producer, client):
        calls.append(body)
        return []

    monkeypatch.setattr(worker, "handle_heartbeat_message", _fake_handle_heartbeat_message)

    bus = InMemoryServiceBus()
    event_consumer = ServiceBusConsumer("event", True, bus)
    task_consumer = ServiceBusConsumer("task", True, bus)
    heartbeat = _heartbeat_body()
    bus.send("event", heartbeat)

    async def _run():
        stop_event = asyncio.Event()
        task = asyncio.create_task(
            worker.run_worker_loop(
                event_consumer,
                task_consumer,
                producer,
                db=None,
                loops={},
                client=bus,
                stop_event=stop_event,
                poll_interval_s=0.05,
            )
        )
        await asyncio.sleep(0.15)
        stop_event.set()
        await asyncio.wait_for(task, timeout=5.0)

    asyncio.run(_run())

    assert len(calls) == 1
    assert calls[0]["event_id"] == heartbeat["event_id"]
