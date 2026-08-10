"""Application-level retry/backoff/dead-letter state machine (C5).

Fully decoupled from the Service Bus transport's maxDeliveryCount=10: the
consumer completes() each message immediately upon accept in the happy
path, and retry_count/backoff/dead-letter live entirely in the
orchestrator's own schema (task_state.retry_count), tracked independently
of any transport-level redelivery count.
"""

from __future__ import annotations

import logging
import random
from typing import Any

from orchestrator.logging_config import get_logger, log_event
from orchestrator.models import TaskStateEnum, TransitionReason

logger = get_logger("state_machine")

# F-DUPLICATE-TERMINAL-REQUEUE (closes the round-23 finding): every state
# a task can never leave once reached. Originally record_failure's own
# idempotency guard only checked DEAD_LETTERED; broadened to the full set
# after finding record_failure could still silently regress an already-
# COMPLETED or already-FAILED task back to RETRY_PENDING if called on it
# directly. Belt-and-suspenders alongside dispatch.TaskAlreadyTerminalError
# (the primary fix, which stops record_failure from ever being reached via
# the not-ready-requeue path for a duplicate message) -- this guard means
# record_failure itself can never corrupt a terminal task's state
# regardless of which caller reaches it.
_TERMINAL_STATES = frozenset(
    {TaskStateEnum.DEAD_LETTERED.value, TaskStateEnum.COMPLETED.value, TaskStateEnum.FAILED.value}
)


def compute_backoff(attempt: int, base_seconds: float = 2.0, jitter_max: float = 0.5) -> float:
    """Exponential backoff with bounded jitter. Spacing between attempts
    (base_seconds * 2**(attempt-1)) exceeds jitter_max, so the sequence is
    monotonically increasing even at jitter's extremes (jitter-proof by
    construction — no flake risk in tests).
    """
    return base_seconds * (2 ** (attempt - 1)) + random.uniform(0, jitter_max)


def record_failure(
    task_id: str,
    db: Any,
    producer: Any = None,
    client: Any = None,
    database_url: str | None = None,
) -> TaskStateEnum:
    """Records one application-level failure of task_id. Dead-letters at
    exactly the 3rd failure (AC-012); the 1st and 2nd leave the task in
    retry_pending.

    Idempotent against redelivery arriving after the task already reached
    ANY terminal state (OR-001; broadened by F-DUPLICATE-TERMINAL-REQUEUE
    — originally this only checked DEAD_LETTERED, see _TERMINAL_STATES'
    module-level comment for why COMPLETED/FAILED were added): reads the
    task's current state first and short-circuits as a no-op — no
    retry_count increment, no re-transition, no re-fired alert — if the
    task is already dead_lettered, completed, or failed.
    """
    current = db.get_task(task_id, database_url=database_url)
    if current is not None and current["state"] in _TERMINAL_STATES:
        log_event(
            logger,
            logging.INFO,
            "record_failure_noop_already_terminal",
            task_id=task_id,
            state=current["state"],
            retry_count=current["retry_count"],
        )
        return TaskStateEnum(current["state"])

    new_retry_count = db.increment_retry(task_id, database_url=database_url)

    if new_retry_count < 3:
        reason = (
            TransitionReason.FAILED_ATTEMPT_1
            if new_retry_count == 1
            else TransitionReason.FAILED_ATTEMPT_2
        )
        db.transition(task_id, TaskStateEnum.RETRY_PENDING, reason, database_url=database_url)
        delay = compute_backoff(new_retry_count)
        log_event(
            logger,
            logging.INFO,
            "task_retry_scheduled",
            task_id=task_id,
            retry_count=new_retry_count,
            backoff_seconds=delay,
        )
        return TaskStateEnum.RETRY_PENDING

    db.transition(
        task_id,
        TaskStateEnum.DEAD_LETTERED,
        TransitionReason.DEAD_LETTERED,
        database_url=database_url,
    )
    log_event(
        logger,
        logging.WARNING,
        "task_dead_lettered",
        task_id=task_id,
        retry_count=new_retry_count,
    )

    from orchestrator.dead_letter import emit_alert

    emit_alert(task_id, db, producer, client, database_url=database_url)
    return TaskStateEnum.DEAD_LETTERED


def cascade_dead_letter(
    task_id: str,
    db: Any,
    blocking_task_id: str,
    producer: Any = None,
    client: Any = None,
    database_url: str | None = None,
) -> TaskStateEnum:
    """2026-08-04: dead-letters task_id IMMEDIATELY, skipping the ordinary
    3-strike retry_pending cycle entirely, because task_id was never
    actually attempted -- it's blocked on `blocking_task_id`, a
    dependency that has itself already reached DEAD_LETTERED and will
    never complete. Called from worker.handle_task_message when
    dispatch.dispatch_task raises DependencyDeadLetteredError.

    Deliberately distinct from record_failure rather than a thin wrapper
    around it:
      - Uses TransitionReason.DEPENDENCY_DEAD_LETTERED, not DEAD_LETTERED,
        so task_transitions can distinguish "we tried 3 times and gave
        up" from "we never tried, it was already impossible" (see that
        enum member's own docstring).
      - Does NOT call db.increment_retry -- task_id's own retry_count
        stays at 0 in the audit trail, since dispatch_task's handler
        genuinely never ran for it (there is nothing to "retry").
      - Skips retry_pending entirely (no 1st/2nd strike, no backoff) --
        waiting would only delay a certainty, not test one, since
        blocking_task_id provably cannot ever complete.

    Same idempotency guarantee as record_failure (OR-001): a no-op if
    task_id is already dead_lettered, so a redelivered/duplicate message
    arriving after this already ran can't double-fire the alert or
    clobber a state a human may have since intervened on.

    Still calls emit_alert, same as record_failure's dead-letter path --
    task_id really is dead-lettered and downstream consumers of that
    alert (AC-012/AC-013) need to know regardless of which path got it
    there.
    """
    current = db.get_task(task_id, database_url=database_url)
    if current is not None and current["state"] == TaskStateEnum.DEAD_LETTERED.value:
        log_event(
            logger,
            logging.INFO,
            "cascade_dead_letter_noop_already_dead_lettered",
            task_id=task_id,
            blocking_task_id=blocking_task_id,
        )
        return TaskStateEnum.DEAD_LETTERED

    db.transition(
        task_id,
        TaskStateEnum.DEAD_LETTERED,
        TransitionReason.DEPENDENCY_DEAD_LETTERED,
        database_url=database_url,
    )
    log_event(
        logger,
        logging.WARNING,
        "task_cascade_dead_lettered",
        task_id=task_id,
        blocking_task_id=blocking_task_id,
    )

    from orchestrator.dead_letter import emit_alert

    emit_alert(task_id, db, producer, client, database_url=database_url)
    return TaskStateEnum.DEAD_LETTERED
