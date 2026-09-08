from __future__ import annotations

import logging
from typing import Any

from orchestrator.dispatch_errors import (
    DependencyDeadLetteredError,
    TaskAlreadyTerminalError,
    TaskNotReadyError,
)
from orchestrator.logging_config import log_event
from orchestrator.models import TaskEnvelope, TaskStateEnum, TransitionReason

from .core import logger
from .table import DISPATCH_TABLE


def legacy_task_pass_through(task_id: str, task_type: str, db: Any) -> None:
    """BYTE-IDENTICAL to worker.py's pre-session unconditional stub
    (RUNNING -> COMPLETED -> advance_dependents). Used for any genuinely
    unregistered task_type -- in that case, if `task_id` has no backing
    task_state row (the e2e test's synthetic 'zzz-unregistered-test-type'
    case), the first db.transition() call's own task_transitions FK
    constraint raises naturally; that propagates to worker.py's existing
    outer try/except (task_handling_failed logged), leaving no unhandled
    exception and no silently-COMPLETED task -- the message is still
    safely completed at transport level regardless (worker.py's `finally`
    block).
    """
    db.transition(task_id, TaskStateEnum.RUNNING, TransitionReason.DISPATCHED)
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)
    log_event(
        logger, logging.INFO, "legacy_task_pass_through", task_id=task_id, task_type=task_type
    )

_PERMANENTLY_BLOCKED_STATES = frozenset(
    {TaskStateEnum.DEAD_LETTERED.value, TaskStateEnum.FAILED.value}
)

# F-DUPLICATE-TERMINAL-REQUEUE: every state a task can NEVER leave once
# reached -- _PERMANENTLY_BLOCKED_STATES (what a *dependency* can be stuck
# in forever) plus COMPLETED (the successful case, only relevant when
# checking THIS task's own state, not a dependency's -- see
# TaskAlreadyTerminalError's docstring).
_TERMINAL_STATES = _PERMANENTLY_BLOCKED_STATES | {TaskStateEnum.COMPLETED.value}

def _find_dead_lettered_dependency(current: dict[str, Any], db: Any) -> dict[str, Any] | None:
    """One-hop check: does `current` (a task row, already known to be
    not-yet-dispatchable) have any depends_on entry that has reached a
    PERMANENT terminal state -- DEAD_LETTERED or FAILED (see
    _PERMANENTLY_BLOCKED_STATES; F-CASCADE-QA-BLOCKED, 4 Aug 2026,
    heartbeat round 17 -- FAILED added alongside the original
    DEAD_LETTERED-only check once a real QA_BLOCKED verdict proved
    equally un-completable and equally in need of a fast cascade).
    Returns that dependency's row (for a precise error message) or None.
    Function name kept as-is despite the broadened check to minimize
    this fix's diff -- see DependencyDeadLetteredError's docstring.

    Deliberately shallow -- see DependencyDeadLetteredError's docstring
    for why a one-hop check is sufficient and a recursive lineage walk
    is not needed here (this is NOT the same as _resolve_dep_lineage
    below, which walks ancestors for a different purpose -- finding a
    real result_ref to build on, not checking for permanent failure)."""
    dep_ids = current.get("depends_on") or []
    if not dep_ids:
        return None
    for dep in db.get_tasks(dep_ids):
        if dep.get("state") in _PERMANENTLY_BLOCKED_STATES:
            return dep
    return None

def dispatch_task(envelope: TaskEnvelope, db: Any) -> None:
    """The one entry point worker.handle_task_message calls. Routes to a
    real handler for every task_type in DISPATCH_TABLE; a genuinely
    unregistered task_type takes the legacy pass-through path unchanged
    from pre-session behaviour.

    F-DISPATCH-GATE: refuses to run ANYTHING (handler or legacy pass-
    through) for a task whose current DB state isn't actually
    dispatchable yet -- see TaskNotReadyError's docstring for why this
    check exists. Previously this function transitioned straight to
    RUNNING and invoked the handler unconditionally, regardless of the
    task's real state, which let a downstream task run (and usually fail,
    permanently, with no retry) before its dependency had genuinely
    completed.
    """
    task_id = str(envelope.task_id)
    current = db.get_task(task_id)
    if current is None or current.get("state") != TaskStateEnum.DISPATCHABLE.value:
        if current is not None and current.get("state") in _TERMINAL_STATES:
            raise TaskAlreadyTerminalError(
                f"task {task_id} ({envelope.task_type}) already reached a "
                f"terminal state ({current['state']}); this message is a "
                "duplicate/redelivery of one already handled",
                current_state=current["state"],
            )
        blocking_dep = None
        if current is not None:
            blocking_dep = _find_dead_lettered_dependency(current, db)
        if blocking_dep is not None:
            blocking_state = blocking_dep.get("state", "unknown")
            raise DependencyDeadLetteredError(
                f"task {task_id} ({envelope.task_type}) can never become "
                f"dispatchable: its dependency {blocking_dep['task_id']} "
                f"({blocking_dep.get('task_type', 'unknown')}) is "
                f"{blocking_state} and will never complete",
                blocking_task_id=blocking_dep["task_id"],
                blocking_task_type=blocking_dep.get("task_type", "unknown"),
            )
        raise TaskNotReadyError(
            f"task {task_id} ({envelope.task_type}) is not dispatchable yet "
            f"(state={current.get('state') if current else 'unknown'}); its "
            "dependencies may not have completed"
        )
    handler = DISPATCH_TABLE.get(envelope.task_type)
    if handler is None:
        # Routed through the `orchestrator.dispatch` package attribute
        # (not the bare module-local name) so a test's
        # monkeypatch.setattr(dispatch, "legacy_task_pass_through", ...)
        # -- which must also cover worker.py's OWN direct reference to
        # dispatch.legacy_task_pass_through in its post-failure retry
        # loop -- patches the exact same binding this call observes.
        # Mirrors worker.py's own local `from orchestrator import
        # dispatch` idiom; safe here because this import is deferred to
        # call time, well after package initialisation completes.
        from orchestrator import dispatch as _dispatch_pkg

        _dispatch_pkg.legacy_task_pass_through(task_id, envelope.task_type, db)
        return
    db.transition(task_id, TaskStateEnum.RUNNING, TransitionReason.DISPATCHED)
    handler(task_id, envelope, db)
