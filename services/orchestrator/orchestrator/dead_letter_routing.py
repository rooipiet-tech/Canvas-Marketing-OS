"""Decides what a dead-letter alert is worth in Teams.

When a task dead-letters, every task downstream of it is dead-lettered
too, without running (state_machine.cascade_dead_letter), and each of
those publishes its own DeadLetterAlert. daily-signal-loop has 23 tasks
and weekly-content-loop 33, so a single failed task near the root posted
23 or 33 near-identical cards at once -- about 55 a day in the OS Approvals
channel -- and the one card that named the real failure was indistinguishable
from the 22 that did not.

This module reads the task's own recorded reason and says which kind of
alert it is. Real failures keep their card, which now also says how many
tasks they took down; cascades are logged but not posted.

The DeadLetterAlert contract is frozen at v1 and metadata-only, so nothing
is added to it: everything here comes from task_transitions and
task_state, which the worker's `db` already reads.

FAILS OPEN. Any lookup problem returns an unknown context and the caller
posts the card exactly as it always did. A noisy channel is recoverable; a
real failure that never reaches anyone is the thing this alert exists to
prevent.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from orchestrator.logging_config import get_logger, log_event, sanitize_exception_text
from orchestrator.models import TransitionReason

logger = get_logger("dead_letter_routing")

KIND_FAILURE = "failure"
KIND_CASCADE = "cascade"

# A safety bound on the walk, not an expected size: the largest loop has 33
# tasks. A malformed depends_on cycle is already stopped by the visited set.
_MAX_DOWNSTREAM = 500


@dataclass(frozen=True)
class DeadLetterContext:
    """`kind` is KIND_FAILURE, KIND_CASCADE, or None when it could not be
    determined (the caller then behaves as before). The downstream fields
    are only meaningful for KIND_FAILURE."""

    kind: str | None = None
    downstream_total: int | None = None
    downstream_by_type: dict[str, int] = field(default_factory=dict)


UNKNOWN = DeadLetterContext()


def _downstream(task_id: str, db: Any) -> tuple[int, dict[str, int]]:
    """Every task transitively blocked by task_id, each counted once.

    Any task that depends on a dead task can never run, however many other
    dependencies it has, so the whole closure is the blast radius.
    """
    seen: set[str] = {task_id}
    frontier = [task_id]
    types: Counter[str] = Counter()
    while frontier and len(seen) - 1 < _MAX_DOWNSTREAM:
        current = frontier.pop()
        for dependent in db.find_dependent_tasks(current):
            dependent_id = dependent["task_id"]
            if dependent_id in seen:
                continue
            seen.add(dependent_id)
            types[dependent["task_type"]] += 1
            frontier.append(dependent_id)
    return len(seen) - 1, dict(types)


def describe(task_id: str, db: Any) -> DeadLetterContext:
    try:
        reason = db.get_dead_letter_reason(task_id)
        if reason == TransitionReason.DEPENDENCY_DEAD_LETTERED.value:
            return DeadLetterContext(kind=KIND_CASCADE)
        if reason != TransitionReason.DEAD_LETTERED.value:
            # No transition recorded, or a vocabulary this code does not know.
            return UNKNOWN
        total, by_type = _downstream(task_id, db)
        return DeadLetterContext(
            kind=KIND_FAILURE, downstream_total=total, downstream_by_type=by_type
        )
    except Exception as exc:  # noqa: BLE001 - fail open, see the module docstring
        log_event(
            logger,
            logging.WARNING,
            "dead_letter_routing_lookup_failed",
            task_id=task_id,
            error=sanitize_exception_text(exc),
        )
        return UNKNOWN
