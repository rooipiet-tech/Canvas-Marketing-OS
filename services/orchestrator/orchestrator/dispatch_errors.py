"""The dispatcher's four exception types (C1 extraction).

MOVED VERBATIM from orchestrator/dispatch.py as part of the C1 split
(remediation backlog wave 3). A PURE MOVE: not a character of the classes
or of the dated incident history in their docstrings has been changed,
only their location.

These sit at the bottom of the dependency graph -- they import nothing
from the dispatcher and everything in it can raise them -- so extracting
them first is what lets any later module raise a DispatchError without a
circular import back into dispatch.py.

Every one is re-exported from dispatch.py, so `dispatch.DispatchError`
and friends keep resolving unchanged for the worker, the handlers and the
tests. They are the SAME class objects, defined once here, so
`except DispatchError` in dispatch.py still catches a DispatchError
raised anywhere else.
"""

from __future__ import annotations


class DispatchError(RuntimeError):
    """A real handler could not complete. Propagates out of dispatch_task
    to worker.py's existing outer try/except (task_handling_failed
    logged), leaving the task at RUNNING for the Service-Bus-redelivery
    backstop (C5, state_machine.record_failure) to eventually reconcile --
    the same fate as any other infra-level dispatch failure, never a
    silent COMPLETED."""

class TaskNotReadyError(RuntimeError):
    """Raised by dispatch_task when a task's queue message was received
    before the task itself actually reached the dispatchable state (i.e.
    its dependencies haven't all completed yet).

    worker.handle_heartbeat_message publishes every task in a decomposed
    batch onto the `task` queue up front, at heartbeat-decompose time --
    NOT gated on any earlier stage actually completing (db.advance_
    dependents only flips a row pending -> dispatchable later, once its
    real predecessor finishes; it never re-publishes anything). With more
    than one orchestrator replica (container-app.bicep's maxReplicas: 3)
    each independently polling the same queue, or simply an out-of-order
    redelivery, a downstream task's message can reach dispatch_task before
    its predecessor's own message has been handled.

    This is deliberately a DIFFERENT exception than DispatchError: a
    handler genuinely failing (bad data, an unreachable dependency) is not
    the same condition as a task whose turn simply hasn't come yet, and the
    two need different recoveries. DispatchError's own docstring assumes a
    "Service-Bus redelivery backstop" will eventually retry a stuck task --
    but worker.run_worker_loop's task-message loop unconditionally calls
    task_consumer.complete(msg) in its `finally`, even after a handler
    exception, so that assumed backstop can never actually fire; a message
    that raises is gone for good, and the task is stuck at RUNNING forever.
    TaskNotReadyError is instead caught by worker.handle_task_message
    itself, which re-publishes the SAME envelope for a later poll pass
    (bounded -- see NOT_READY_MAX_REQUEUES) rather than ever calling the
    handler on a not-yet-ready task or losing the message.

    dispatch_task only raises this for a dependency that is still
    genuinely in flight (pending/running/retry_pending) -- one worth
    waiting on. A dependency that has already reached a PERMANENT
    terminal state -- DEAD_LETTERED (3-strike retry exhaustion) or
    FAILED (e.g. TransitionReason.QA_BLOCKED: a real, non-retryable
    business verdict -- see qa_review_handler) -- raises
    DependencyDeadLetteredError instead (see its own docstring): that
    dependency will NEVER complete, so bouncing this message back onto
    the queue for NOT_READY_MAX_REQUEUES more polls before falling
    through to the ordinary 3-strike retry/backoff cycle just delays an
    outcome that is already certain (2026-08-04 finding: this stacked
    the 20-requeue not-ready bound in series with a FRESH 3-strike
    record_failure cycle, ~15+ minutes end-to-end for a task blocked on
    a permanently-failed dependency to reach its own terminal state --
    see DependencyDeadLetteredError for the fix).

    F-CASCADE-QA-BLOCKED (4 Aug 2026, heartbeat round 17): originally
    this check covered DEAD_LETTERED only. Once F-QA-REVIEW-PUBLIC-
    SOURCE let qa-review actually run to completion against real
    draft-brief content for the first time (instead of always dying
    upstream at the redaction firewall), it produced its first-ever
    real QA_BLOCKED verdict in production -- and that verdict's
    dependent (publish-brief) was found stuck not-ready-requeuing for
    the entire ~15 minute stacked-timeout window, never cascading,
    because FAILED wasn't recognized as equally permanent. A QA_BLOCKED
    draft is exactly as un-completable as a dead-lettered one: nothing
    retries a FAILED task automatically (record_failure never produces
    it; only qa_review_handler does, deliberately, as a one-shot
    business outcome), so a downstream task waiting on one has nothing
    left to wait for either."""

class TaskAlreadyTerminalError(RuntimeError):
    """Raised by dispatch_task instead of TaskNotReadyError when task_id's
    OWN current state has already reached a terminal state (COMPLETED,
    DEAD_LETTERED, or FAILED) rather than merely not-yet-dispatchable.

    F-DUPLICATE-TERMINAL-REQUEUE (11 Aug 2026, closes the round-23
    finding): at-least-once queue delivery -- Service Bus redelivery
    after a lock/visibility-timeout lapse, or, per TaskNotReadyError's own
    docstring, more than one orchestrator replica independently polling
    the same queue -- can deliver a SECOND copy of a task's own message
    after the first copy already ran it to completion (or dead-lettered
    it, or QA-blocked it). Before this fix, dispatch_task's not-ready gate
    could not tell "my dependencies haven't finished yet, and WILL"
    (current.state not yet DISPATCHABLE, worth waiting on) apart from "I
    MYSELF already finished, and never will change again" (current.state
    is COMPLETED/DEAD_LETTERED/FAILED) -- both fell into the same
    `current.state != DISPATCHABLE` branch and both raised
    TaskNotReadyError. A duplicate message for an already-terminal task
    would then requeue NOT_READY_MAX_REQUEUES (20) times for no reason
    (nothing is ever going to change), hit worker.py's
    task_not_ready_giving_up path, and call state_machine.record_failure
    -- which was idempotent against redelivery landing on an already-
    DEAD_LETTERED task (OR-001), but NOT against one that's already
    COMPLETED or FAILED: it would increment retry_count and transition
    the task straight back to RETRY_PENDING, silently overwriting a
    genuinely-finished task's terminal state in the DB with no obvious
    external symptom (the queue message itself is discarded either way,
    per run_worker_loop's unconditional finally-complete) -- a real
    state-corruption bug hiding behind what looked, from the outside,
    like a harmless no-op. (state_machine.record_failure's own
    idempotency guard is now broadened to cover this directly too, as a
    second layer -- see its docstring -- but the fix here is what stops
    the ~15-minute, 20-requeue detour from ever starting.)

    worker.handle_task_message catches this and treats it as exactly what
    it is: an idempotent duplicate. No requeue, no retry, no dead-letter,
    no state_machine call at all -- the task's state is already final and
    correct; the only thing left to do is log it and discard the
    redundant message."""

    def __init__(self, message: str, current_state: str) -> None:
        super().__init__(message)
        self.current_state = current_state

class DependencyDeadLetteredError(RuntimeError):
    """Raised by dispatch_task instead of TaskNotReadyError when the task
    isn't dispatchable yet AND at least one of its depends_on entries has
    already reached a PERMANENT terminal state -- DEAD_LETTERED or FAILED
    (checked one hop up, not the full lineage -- see below for why that's
    sufficient). Named for its original, narrower DEAD_LETTERED-only
    scope; kept rather than renamed (F-CASCADE-QA-BLOCKED, 4 Aug 2026) to
    keep this fix's diff minimal -- every reference to "dead lettered" in
    this class and its docstring should be read as "permanently blocked
    (dead_lettered or failed)".

    A task can only become `dispatchable` once EVERY entry in depends_on
    has COMPLETED (db.advance_dependents' contract). If any one of them
    is instead permanently DEAD_LETTERED or FAILED, that condition can
    never be satisfied -- the ordinary not-ready path (TaskNotReadyError:
    retry later, the dependency is still working) does not apply, because
    there is nothing left to wait for.

    worker.handle_task_message catches this and calls
    state_machine.cascade_dead_letter immediately -- no requeue, no
    backoff, no 3-strike cycle -- so a task blocked on a permanently
    failed dependency reaches its own terminal state in the same
    message pass that discovers the block, not ~15 minutes later.

    One-hop-only is intentional, not a shortcut: if an ANCESTOR further
    up the chain (rather than an immediate dependency) is the one that
    dead-lettered, the immediate dependency will itself be cascade-
    dead-lettered the next time ITS own not-ready gate is checked (the
    same wave-by-wave propagation this whole gate mechanism already
    relies on for the ordinary not-ready case), which in turn cascades
    to this task on ITS next check. No recursive lineage walk needed."""

    def __init__(self, message: str, blocking_task_id: str, blocking_task_type: str) -> None:
        super().__init__(message)
        # Structured access for worker.py's handler -- avoids parsing the
        # message string back apart to find which dependency caused this.
        self.blocking_task_id = blocking_task_id
        self.blocking_task_type = blocking_task_type
