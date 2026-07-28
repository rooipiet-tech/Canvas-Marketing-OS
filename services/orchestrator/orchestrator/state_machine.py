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
    """
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
