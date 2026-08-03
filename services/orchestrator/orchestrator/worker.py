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
from orchestrator.logging_config import get_logger, log_event, sanitize_exception_text
from orchestrator.models import HeartbeatEvent, LoopDefinition, TaskEnvelope

logger = get_logger("worker")

# F-DISPATCH-GATE backstop: how many times handle_task_message will bounce
# a not-yet-ready task's message back onto the `task` queue (at
# poll_interval_s cadence, ~1s in production -- see run_worker_loop) before
# giving up and routing it through the real retry/dead-letter state
# machine instead of requeuing forever. A real predecessor stage normally
# finishes in well under a minute; this is deliberately generous relative
# to that.
NOT_READY_MAX_REQUEUES = 60


def _task_metadata(params: dict[str, Any] | None) -> dict[str, str] | None:
    """Stringifies the loop YAML task's `params` dict onto the wire
    envelope's `metadata` bag (task-metadata only, never client/personal
    data). Only `proof_circuit` is carried through today -- the one flag
    dispatch.py's qa-review handler actually reads (AC-31(c)) -- other
    params keys (e.g. weekly-content-loop.yaml's function_id/action_class/
    channel_ids) are left for their own consumers to read directly off the
    loop definition where needed; nothing here is a general params-bag
    passthrough."""
    if not params:
        return None
    proof_circuit = params.get("proof_circuit")
    if proof_circuit is None:
        return None
    return {"proof_circuit": "true" if proof_circuit else "false"}


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
    await asyncio.to_thread(db.insert_task_batch, tasks)

    published: list[dict[str, Any]] = []
    for task in tasks:
        envelope = TaskEnvelope(
            task_id=task["task_id"],
            task_type=task["task_type"],
            agent_run_id=uuid.uuid5(heartbeat.event_id, f"agent_run:{task['source_task_id']}"),
            campaign_id=uuid.uuid5(heartbeat.event_id, f"campaign:{loop.loop_id}"),
            created_at=datetime.now(timezone.utc),
            retry_count=0,
            # Task-metadata-only bag (frozen contract's own `metadata`
            # field, string->string) -- carries the loop YAML's
            # `params.proof_circuit` flag (decompose.py) so dispatch.py's
            # qa-review handler can tell which of its two loop positions
            # (brief-QA vs. proof-circuit content-QA) it's being invoked
            # from, without any frozen-contract change. Never client/
            # personal data.
            metadata=_task_metadata(task.get("params")),
        )
        envelope_dict = envelope.to_wire_dict()
        await asyncio.to_thread(producer.publish, "task", envelope_dict, client)
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
    (delivery_count == 1).

    dispatch.dispatch_task (plan step 6+) is now the real per-task-type
    router: the 5 GOAL-mandated task_types (ingest-signals, draft-brief,
    qa-review, draft-content, request-approval) get a real handler that
    produces a real downstream artifact (AC-01); every other task_type —
    including every already-real S10/S11 task_type — falls through to
    dispatch.legacy_task_pass_through, which is BYTE-IDENTICAL to this
    function's own pre-session behaviour (RUNNING -> COMPLETED ->
    advance_dependents), so nothing here regresses (AC-02). Run off the
    event loop's thread pool: dispatch_task makes blocking HTTP calls
    (gateway/Vault/Gatekeeper/mcp-web) as well as blocking DB calls.
    """
    from orchestrator import dispatch

    envelope = TaskEnvelope.model_validate(body)
    task_id = str(envelope.task_id)
    try:
        await asyncio.to_thread(dispatch.dispatch_task, envelope, db)
    except dispatch.TaskNotReadyError as exc:
        # This task's message arrived before it actually reached the
        # dispatchable state (its dependencies aren't all done yet -- see
        # TaskNotReadyError's docstring). Bounce it back onto the `task`
        # queue for a later poll pass instead of running it early or
        # letting run_worker_loop's own except/finally silently discard it
        # for good (that path's assumed Service-Bus-redelivery backstop
        # never actually fires, since every task message is unconditionally
        # completed regardless of outcome). Bounded so a task whose
        # dependency never completes doesn't requeue forever.
        if envelope.retry_count >= NOT_READY_MAX_REQUEUES:
            log_event(
                logger,
                logging.ERROR,
                "task_not_ready_giving_up",
                task_id=task_id,
                task_type=envelope.task_type,
                requeue_count=envelope.retry_count,
                error=sanitize_exception_text(exc),
            )
            await asyncio.to_thread(state_machine.record_failure, task_id, db, producer, client)
            return
        log_event(
            logger,
            logging.INFO,
            "task_not_ready_requeued",
            task_id=task_id,
            task_type=envelope.task_type,
            requeue_count=envelope.retry_count,
        )
        bounced = envelope.model_copy(update={"retry_count": envelope.retry_count + 1})
        await asyncio.to_thread(producer.publish, "task", bounced.to_wire_dict(), client)
        return
    log_event(
        logger, logging.INFO, "task_dispatched", task_id=task_id, task_type=envelope.task_type
    )


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
    await asyncio.to_thread(state_machine.record_failure, task_id, db, producer, client)


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
            event_messages = await asyncio.to_thread(event_consumer.receive, max_count=10)
        except Exception as exc:  # noqa: BLE001 - one bad receive must not kill the loop
            log_event(
                logger, logging.ERROR, "event_receive_failed", error=sanitize_exception_text(exc)
            )
            event_messages = []

        for msg in event_messages:
            try:
                await handle_heartbeat_message(msg.body, loops, db, producer, client)
            except Exception as exc:  # noqa: BLE001
                log_event(
                    logger,
                    logging.ERROR,
                    "heartbeat_handling_failed",
                    error=sanitize_exception_text(exc),
                )
            finally:
                try:
                    await asyncio.to_thread(event_consumer.complete, msg)
                except Exception:  # noqa: BLE001
                    pass

        try:
            task_messages = await asyncio.to_thread(task_consumer.receive, max_count=10)
        except Exception as exc:  # noqa: BLE001
            log_event(
                logger, logging.ERROR, "task_receive_failed", error=sanitize_exception_text(exc)
            )
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
                    error=sanitize_exception_text(exc),
                )
            finally:
                try:
                    await asyncio.to_thread(task_consumer.complete, msg)
                except Exception:  # noqa: BLE001
                    pass

        if stop_event.is_set():
            break

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=poll_interval_s)
        except asyncio.TimeoutError:
            pass
