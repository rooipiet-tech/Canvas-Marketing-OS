"""The background consumer/dispatch loop that makes the deployed app
actually consume what it publishes (F1 — the central fix of plan v2).

main.py's FastAPI lifespan spawns run_worker_loop as a long-lived
asyncio.Task on startup. It polls the `event` queue, decomposes
heartbeats, persists the resulting task batch, and publishes task
envelopes onto the `task` queue; it also polls the `task` queue itself and
drives real state transitions — including reconcile_redelivered_task,
which turns a Service-Bus-redelivered message (delivery_count > 1,
signalling a real process crash between receive and complete) into an
application-level failure via state_machine.record_failure (the C5
"backstop" case).

All four functions here are exercised directly by tests against the
local/emulated Service Bus double (C4) AND are the exact functions the
deployed app's worker loop calls in production — no test-only
reimplementation.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from orchestrator import decompose, state_machine
from orchestrator.logging_config import get_logger, log_event
from orchestrator.models import (
    HeartbeatEvent,
    LoopDefinition,
    TaskEnvelope,
    TaskStateEnum,
    TransitionReason,
)

logger = get_logger("worker")


async def handle_heartbeat_message(
    body: dict[str, Any],
    loops: dict[str, LoopDefinition],
    db: Any,
    producer: Any,
    client: Any,
) -> list[dict[str, Any]]:
    """Validates body against HeartbeatEvent, decomposes it against the
    matching loop, persists the batch, publishes one TaskEnvelope per task
    onto the `task` queue, and returns the published envelopes.
    """
    heartbeat = HeartbeatEvent.model_validate(body)
    loop = loops.get(heartbeat.loop_id)
    if loop is None:
        log_event(
            logger,
            logging.WARNING,
            "heartbeat_unknown_loop",
            loop_id=heartbeat.loop_id,
            event_id=str(heartbeat.event_id),
        )
        return []

    tasks = decompose.decompose(loop, heartbeat)
    db.insert_task_batch(tasks)

    published: list[dict[str, Any]] = []
    for task in tasks:
        envelope = TaskEnvelope(
            task_id=task["task_id"],
            task_type=task["task_type"],
            agent_run_id=uuid.uuid5(heartbeat.event_id, f"agent_run:{task['source_task_id']}"),
            campaign_id=uuid.uuid5(heartbeat.event_id, f"campaign:{loop.loop_id}"),
            created_at=datetime.now(timezone.utc),
            retry_count=0,
        )
        envelope_dict = envelope.to_wire_dict()
        producer.publish("task", envelope_dict, client)
        published.append(envelope_dict)

    log_event(
        logger,
        logging.INFO,
        "heartbeat_decomposed",
        loop_id=loop.loop_id,
        event_id=str(heartbeat.event_id),
        task_count=len(tasks),
    )
    return published


async def handle_task_message(body: dict[str, Any], db: Any, producer: Any, client: Any) -> None:
    """Happy-path handler for a freshly-dispatched task envelope
    (delivery_count == 1). This session's goal is to track/dispatch tasks,
    not perform their real downstream work, so a successful receive
    immediately drives the task to completed and advances its dependents
    (AC-010's mechanism).
    """
    envelope = TaskEnvelope.model_validate(body)
    task_id = str(envelope.task_id)
    db.transition(task_id, TaskStateEnum.RUNNING, TransitionReason.DISPATCHED)
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)
    log_event(logger, logging.INFO, "task_completed", task_id=task_id)


async def reconcile_redelivered_task(msg: Any, db: Any, producer: Any, client: Any) -> None:
    """Called instead of handle_task_message when msg.delivery_count > 1 —
    the signature of a real process crash between receive and complete
    (the native PeekLock-expiry backstop C5 describes). Routes the task
    through the application-level retry/dead-letter state machine.
    """
    body = msg.body
    task_id = body.get("task_id")
    if not task_id:
        log_event(
            logger,
            logging.WARNING,
            "redelivered_message_missing_task_id",
            queue=getattr(msg, "queue", None),
        )
        return
    log_event(
        logger,
        logging.WARNING,
        "task_redelivered_reconciling",
        task_id=task_id,
        delivery_count=msg.delivery_count,
    )
    state_machine.record_failure(task_id, db, producer, client)


async def run_worker_loop(
    event_consumer: Any,
    task_consumer: Any,
    producer: Any,
    db: Any,
    loops: dict[str, LoopDefinition],
    client: Any,
    stop_event: asyncio.Event,
    poll_interval_s: float = 1.0,
) -> None:
    """Long-lived poll loop. Intentionally infinite while the deployed app
    is up (governed only by stop_event, never an iteration count) — the
    "no unbounded loop" rule in AC-007/AC-022/AC-028 targets CI/deploy-time
    *polling scripts*, not this application-level worker process.

    Every message's handling is wrapped so one malformed/failing message
    never crashes the loop itself — it is logged and the message is still
    completed (moved past).
    """
    while not stop_event.is_set():
        try:
            event_messages = event_consumer.receive(max_count=10)
        except Exception as exc:  # noqa: BLE001 - one bad receive must not kill the loop
            log_event(logger, logging.ERROR, "event_receive_failed", error=str(exc))
            event_messages = []

        for msg in event_messages:
            try:
                await handle_heartbeat_message(msg.body, loops, db, producer, client)
            except Exception as exc:  # noqa: BLE001
                log_event(
                    logger,
                    logging.ERROR,
                    "heartbeat_handling_failed",
                    error=str(exc),
                )
            finally:
                try:
                    event_consumer.complete(msg)
                except Exception:  # noqa: BLE001
                    pass

        try:
            task_messages = task_consumer.receive(max_count=10)
        except Exception as exc:  # noqa: BLE001
            log_event(logger, logging.ERROR, "task_receive_failed", error=str(exc))
            task_messages = []

        for msg in task_messages:
            try:
                if msg.delivery_count > 1:
                    await reconcile_redelivered_task(msg, db, producer, client)
                else:
                    await handle_task_message(msg.body, db, producer, client)
            except Exception as exc:  # noqa: BLE001
                log_event(
                    logger,
                    logging.ERROR,
                    "task_handling_failed",
                    error=str(exc),
                )
            finally:
                try:
                    task_consumer.complete(msg)
                except Exception:  # noqa: BLE001
                    pass

        if stop_event.is_set():
            break

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=poll_interval_s)
        except asyncio.TimeoutError:
            pass
