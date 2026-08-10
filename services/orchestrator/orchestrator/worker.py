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
# a not-yet-ready task's message back onto the `task` queue before giving
# up and routing it through the real retry/dead-letter state machine
# instead of requeuing forever.
#
# Tuned 2026-08-03 against the REAL observed production cadence, not a
# theoretical one: config.WORKER_POLL_INTERVAL_S defaults to 1s, but
# Log Analytics for a live run showed ~14s between successive requeues of
# the SAME task (a shared queue carrying an entire heartbeat's ~20+ tasks,
# each replica pulling max_count=10 per poll, means any one task's own
# resubmitted message realistically waits multiple poll cycles behind its
# siblings before it comes back around). caj-loop-e2e-smoke's own poll
# budget is MAX_ATTEMPTS(40) x SLEEP_SECONDS(15) = 600s; the previous
# bound of 60 x ~14s ~= 840s could never resolve inside that window even
# once every real bug upstream was fixed, so a permanently-blocked
# dependency would always burn the full smoke budget before this backstop
# ever fired. 20 x ~14s ~= 280s leaves comfortable headroom under 600s
# while still being far more generous than any real predecessor stage
# observed here (single-digit seconds).
NOT_READY_MAX_REQUEUES = 20


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


def _event_message_kind(body: Any) -> str:
    """Discriminates the two message shapes the `event` queue carries
    today (F-EVENTQ-DISCRIMINATE, 4 Aug 2026, heartbeat round 14): a
    HeartbeatEvent (`event_type == "heartbeat"`, the only kind
    run_worker_loop's event-queue handling ever consumed before this fix)
    and a DeadLetterAlert (dead_letter.py's `emit_alert`, AC-012/AC-013 --
    shares this queue by design, but its own frozen contract,
    contracts/orchestrator/dead-letter-alert.schema.json, has no
    `event_type` field at all; its `alert_version` field is what
    distinguishes it instead).

    Before this fix, EVERY message pulled off `event` -- including every
    DeadLetterAlert -- was force-validated as a HeartbeatEvent, so every
    single task dead-letter crashed this with 8 Pydantic errors (caught by
    run_worker_loop's own try/except, non-fatal, but pure log noise on
    every dead-letter event; confirmed reproducible in heartbeat rounds
    13 and 14).

    Returns "heartbeat", "dead_letter_alert", or "unknown" -- any other or
    malformed shape still falls through to the original
    HeartbeatEvent.model_validate path in the caller, so a genuinely
    malformed heartbeat still fails loudly exactly as it did before this
    fix. Neither frozen contract is touched by this function or by this
    fix -- it is pure routing over the two contracts' already-distinct
    required-field shapes.
    """
    if not isinstance(body, dict):
        return "unknown"
    if body.get("event_type") == "heartbeat":
        return "heartbeat"
    if "alert_version" in body:
        return "dead_letter_alert"
    return "unknown"


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
    except dispatch.DependencyDeadLetteredError as exc:
        # 2026-08-04: this task can never become dispatchable -- one of
        # its dependencies already reached a permanent terminal state
        # (DEAD_LETTERED, or FAILED e.g. via a real QA_BLOCKED verdict --
        # see F-CASCADE-QA-BLOCKED, 4 Aug 2026, heartbeat round 17, and
        # dispatch.py's _PERMANENTLY_BLOCKED_STATES) and will never
        # complete. Distinct from the ordinary not-ready path just below:
        # there is nothing to wait for, so dead-letter task_id right now
        # instead of bouncing it through NOT_READY_MAX_REQUEUES requeues
        # followed by a fresh 3-strike record_failure cycle (~15+ minutes
        # to reach the exact same outcome this reaches immediately).
        log_event(
            logger,
            logging.WARNING,
            "task_cascade_dead_lettering",
            task_id=task_id,
            task_type=envelope.task_type,
            blocking_task_id=exc.blocking_task_id,
            blocking_task_type=exc.blocking_task_type,
        )
        await asyncio.to_thread(
            state_machine.cascade_dead_letter,
            task_id,
            db,
            exc.blocking_task_id,
            producer,
            client,
        )
        return
    except dispatch.TaskAlreadyTerminalError as exc:
        # F-DUPLICATE-TERMINAL-REQUEUE (closes the round-23 finding):
        # this task's own message has already been handled once -- its
        # state is already COMPLETED/DEAD_LETTERED/FAILED and will never
        # change again (at-least-once queue delivery redelivered a
        # duplicate copy). This is a normal, expected duplicate, not an
        # error: nothing to dispatch, nothing to requeue, nothing to
        # dead-letter (it may already be dead-lettered), and definitely
        # nothing to route through state_machine.record_failure -- see
        # TaskAlreadyTerminalError's docstring for the state-corruption
        # bug that path used to hit when reached this way. The message is
        # simply redundant; run_worker_loop's own finally discards it
        # either way.
        log_event(
            logger,
            logging.INFO,
            "task_message_duplicate_already_terminal",
            task_id=task_id,
            task_type=envelope.task_type,
            current_state=exc.current_state,
        )
        return
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
    except Exception as exc:  # noqa: BLE001 - F-DISPATCH-RETRY (2026-08-03): a genuine
        # handler failure (bad upstream data, a REDACTION_BLOCKED gateway
        # response, an unreachable dependency, anything dispatch_task's
        # handler itself raises once the not-ready gate has already been
        # cleared) is NOT a TaskNotReadyError and was previously left
        # completely unhandled here -- it propagated to run_worker_loop's
        # own outer try/except, got logged, and the task stayed at
        # `running` forever with zero retry, since that loop unconditionally
        # completes the queue message in its `finally` regardless of
        # outcome (the exact "assumed backstop never fires" problem
        # TaskNotReadyError's docstring already describes for the
        # not-ready case -- this is the SAME problem for every other
        # failure mode). Route it through the real retry/dead-letter state
        # machine instead, so it reaches a genuine terminal state.
        await _retry_or_dead_letter(envelope, task_id, exc, db, producer, client)
        return
    log_event(
        logger, logging.INFO, "task_dispatched", task_id=task_id, task_type=envelope.task_type
    )


async def _retry_or_dead_letter(
    envelope: TaskEnvelope, task_id: str, exc: Exception, db: Any, producer: Any, client: Any
) -> None:
    """dispatch_task already transitioned task_id to `running` and invoked
    its handler once before `exc` was raised (see F-DISPATCH-RETRY above).
    Re-entering dispatch_task's own not-ready gate here would just be
    rejected (the task's real DB state is `running`, not `dispatchable`,
    and after the first state_machine.record_failure call below it becomes
    `retry_pending`, still not `dispatchable`) -- so a genuine failure
    retries the SAME handler directly, up to state_machine.record_failure's
    own 3-strike limit, rather than re-queuing onto the `task` queue.

    Known limitation, accepted deliberately rather than left implicit: a
    handler that partially wrote to Vault before failing is not guaranteed
    idempotent on retry (e.g. a duplicate signal/agent_run row is
    possible). This is the same risk profile any retry of a non-atomic
    multi-step handler carries; there is no queue-transport-level redelivery
    here to inherit that problem from instead (see F-DISPATCH-RETRY).
    """
    from orchestrator import dispatch

    log_event(
        logger,
        logging.ERROR,
        "task_dispatch_failed",
        task_id=task_id,
        task_type=envelope.task_type,
        error=sanitize_exception_text(exc),
    )
    outcome = await asyncio.to_thread(state_machine.record_failure, task_id, db, producer, client)
    handler = dispatch.DISPATCH_TABLE.get(envelope.task_type)
    while outcome.value == "retry_pending":
        await asyncio.sleep(2.0)
        try:
            if handler is None:
                await asyncio.to_thread(
                    dispatch.legacy_task_pass_through, task_id, envelope.task_type, db
                )
            else:
                await asyncio.to_thread(handler, task_id, envelope, db)
            log_event(
                logger,
                logging.INFO,
                "task_dispatch_retry_succeeded",
                task_id=task_id,
                task_type=envelope.task_type,
            )
            return
        except Exception as retry_exc:  # noqa: BLE001 - same class of failure, one more attempt
            log_event(
                logger,
                logging.ERROR,
                "task_dispatch_retry_failed",
                task_id=task_id,
                task_type=envelope.task_type,
                error=sanitize_exception_text(retry_exc),
            )
            outcome = await asyncio.to_thread(
                state_machine.record_failure, task_id, db, producer, client
            )
    # outcome is now dead_lettered (or was already, per record_failure's
    # own idempotent no-op) -- a genuine terminal state; nothing more to do.


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
                kind = _event_message_kind(msg.body)
                if kind == "dead_letter_alert":
                    # F-EVENTQ-DISCRIMINATE: informational only today --
                    # nothing in the worker loop consumes DeadLetterAlert
                    # yet (AC-012/AC-013 only require it be emitted and
                    # observable). Acknowledge and move on rather than
                    # forcing it through HeartbeatEvent.model_validate.
                    log_event(
                        logger,
                        logging.INFO,
                        "dead_letter_alert_received",
                        task_id=msg.body.get("task_id"),
                        task_type=msg.body.get("task_type"),
                    )
                else:
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
