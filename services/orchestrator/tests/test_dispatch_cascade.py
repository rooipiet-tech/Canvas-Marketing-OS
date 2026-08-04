"""F-DISPATCH-CASCADE (2026-08-04): a task blocked on a dependency that
has ALREADY reached DEAD_LETTERED must fail fast, not requeue.

Root-cause context, live: heartbeat attempt 11 (deploy-loop-e2e-smoke #25,
run 30864982147) confirmed both F-DISPATCH-GATE (test_dispatch_gate.py)
and F-DISPATCH-RETRY (test_dispatch_retry.py) working correctly -- stage 1
(ingest-signals) hit a real REDACTION_BLOCKED response and dead-lettered
in 6 seconds. But its 13 direct dependents then sat blocked: each one's
not-ready gate correctly detected the dependency wasn't dispatchable, but
had no way to tell "still working, worth waiting" apart from "permanently
dead, will NEVER complete" -- so every one of them ran the FULL ordinary
not-ready path (TaskNotReadyError, NOT_READY_MAX_REQUEUES=20 requeues,
~280s) and then, on giving up, fell into a FRESH 3-strike
state_machine.record_failure cycle (another ~2 rounds of the same ~280s
shape) before finally reaching DEAD_LETTERED themselves -- roughly 15+
minutes end-to-end, well past the smoke test's 10-minute bounded poll.
Confirmed via Log Analytics: all 13 blocked tasks gave up their first
20-requeue round by ~00:29:10 UTC, one dead-lettered ingest-signals at
00:23:52 -- the not-ready gate had no way to short-circuit even though
the outcome was already certain from the very first check.

This is the fix: dispatch_task now raises DependencyDeadLetteredError
(not TaskNotReadyError) when a blocking dependency is already
DEAD_LETTERED, and worker.handle_task_message dead-letters the blocked
task IMMEDIATELY via state_machine.cascade_dead_letter -- no requeue, no
backoff, no 3-strike cycle -- so a permanently-blocked task reaches its
own terminal state in the same message pass that discovers the block.

Uses tests/fakes-style in-memory doubles (no live Postgres/Service Bus),
matching test_dispatch_gate.py and test_dispatch_retry.py -- duplicated
rather than imported, since this package has no __init__.py.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
from orchestrator import dispatch, state_machine, worker
from orchestrator.models import TaskEnvelope, TaskStateEnum
from orchestrator.servicebus import producer
from orchestrator.servicebus.local_double import InMemoryServiceBus


class FakeTaskDB:
    """Same in-memory surface as test_dispatch_gate.py/test_dispatch_retry.py's
    FakeTaskDB, plus a depends_on kwarg on seed() -- needed here since
    this file is specifically about dependency-chain behaviour."""

    def __init__(self) -> None:
        self.tasks: dict[str, dict[str, Any]] = {}

    def seed(
        self,
        task_id: str,
        task_type: str,
        *,
        state: str = "dispatchable",
        depends_on: list[str] | None = None,
    ) -> None:
        self.tasks[task_id] = {
            "task_id": task_id,
            "loop_id": "test-loop",
            "task_type": task_type,
            "state": state,
            "depends_on": depends_on or [],
            "result_ref": None,
            "retry_count": 0,
        }

    def get_task(self, task_id: str, database_url: str | None = None) -> dict[str, Any] | None:
        return self.tasks.get(task_id)

    def get_tasks(
        self, task_ids: list[str], database_url: str | None = None
    ) -> list[dict[str, Any]]:
        return [self.tasks[t] for t in task_ids if t in self.tasks]

    def transition(self, task_id: str, to_state, reason, database_url: str | None = None) -> None:
        to_state_val = to_state.value if hasattr(to_state, "value") else to_state
        if task_id not in self.tasks:
            raise RuntimeError(f"no such task {task_id}")
        self.tasks[task_id]["state"] = to_state_val

    def advance_dependents(
        self, completed_task_id: str, database_url: str | None = None
    ) -> list[str]:
        return []

    def increment_retry(self, task_id: str, database_url: str | None = None) -> int:
        self.tasks[task_id]["retry_count"] += 1
        return self.tasks[task_id]["retry_count"]


def _envelope(task_id: str, task_type: str, *, retry_count: int = 0) -> TaskEnvelope:
    return TaskEnvelope(
        task_id=uuid.UUID(task_id),
        task_type=task_type,
        agent_run_id=uuid.uuid4(),
        campaign_id=uuid.uuid4(),
        created_at=datetime.now(timezone.utc),
        retry_count=retry_count,
    )


def test_dispatch_task_raises_dependency_dead_lettered_not_task_not_ready():
    """The core distinction this fix introduces: a task blocked on a
    DEAD_LETTERED dependency must NOT raise the ordinary TaskNotReadyError
    (which would trigger 20 rounds of pointless requeuing) -- it must
    raise DependencyDeadLetteredError instead, carrying which dependency
    is the blocker."""
    db = FakeTaskDB()
    dep_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())
    db.seed(dep_id, "ingest-signals", state=TaskStateEnum.DEAD_LETTERED.value)
    db.seed(task_id, "qa-review", state="pending", depends_on=[dep_id])

    with pytest.raises(dispatch.DependencyDeadLetteredError) as exc_info:
        dispatch.dispatch_task(_envelope(task_id, "qa-review"), db)

    assert exc_info.value.blocking_task_id == dep_id
    assert exc_info.value.blocking_task_type == "ingest-signals"
    # State must be untouched by the raise itself -- worker.py's handler
    # is what actually transitions it.
    assert db.get_task(task_id)["state"] == "pending"


def test_dispatch_task_still_raises_task_not_ready_when_dependency_in_flight():
    """A task whose dependency is merely still running (not permanently
    failed) must still get the ordinary, patient TaskNotReadyError --
    this fix must not make the not-ready path more aggressive for the
    normal, expected case."""
    db = FakeTaskDB()
    dep_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())
    db.seed(dep_id, "ingest-signals", state="running")
    db.seed(task_id, "qa-review", state="pending", depends_on=[dep_id])

    with pytest.raises(dispatch.TaskNotReadyError):
        dispatch.dispatch_task(_envelope(task_id, "qa-review"), db)

    assert db.get_task(task_id)["state"] == "pending"


def test_handle_task_message_cascade_dead_letters_immediately_no_requeue():
    """The end-to-end regression this fix targets: a task blocked on a
    dead-lettered dependency must reach DEAD_LETTERED itself in ONE
    message pass -- not requeued onto the `task` queue at all, and
    critically NOT sent through state_machine.record_failure's 3-strike
    retry_pending cycle (which would take ~15 minutes across two more
    bounded-requeue rounds to reach the same outcome)."""
    db = FakeTaskDB()
    dep_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())
    db.seed(dep_id, "ingest-signals", state=TaskStateEnum.DEAD_LETTERED.value)
    db.seed(task_id, "qa-review", state="pending", depends_on=[dep_id])

    bus = InMemoryServiceBus()
    envelope = _envelope(task_id, "qa-review")

    asyncio.run(worker.handle_task_message(envelope.to_wire_dict(), db, producer, bus))

    assert db.get_task(task_id)["state"] == TaskStateEnum.DEAD_LETTERED.value
    # Never went through retry_pending -- retry_count stays at 0, since
    # the handler genuinely never ran (nothing to "retry").
    assert db.get_task(task_id)["retry_count"] == 0
    # Never requeued onto `task` -- no wasted poll rounds.
    assert bus.queue_depth("task") == 0


def test_cascade_dead_letter_uses_distinct_transition_reason():
    """task_transitions must be able to tell "we tried 3 times and gave
    up" (DEAD_LETTERED reason) apart from "we never tried, it was already
    impossible" (DEPENDENCY_DEAD_LETTERED reason) -- verified directly
    against state_machine.cascade_dead_letter since FakeTaskDB.transition
    only records the resulting state, not the reason it was called with."""
    from orchestrator.models import TransitionReason

    calls: list[Any] = []
    db = FakeTaskDB()
    dep_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())
    db.seed(dep_id, "ingest-signals", state=TaskStateEnum.DEAD_LETTERED.value)
    db.seed(task_id, "qa-review", state="pending", depends_on=[dep_id])

    real_transition = db.transition

    def spy_transition(task_id_, to_state, reason, database_url=None):
        calls.append(reason)
        return real_transition(task_id_, to_state, reason, database_url=database_url)

    db.transition = spy_transition  # type: ignore[method-assign]

    bus = InMemoryServiceBus()
    state_machine.cascade_dead_letter(task_id, db, dep_id, producer, bus)

    assert TransitionReason.DEPENDENCY_DEAD_LETTERED in calls
    assert TransitionReason.DEAD_LETTERED not in calls


def test_cascade_dead_letter_is_idempotent_noop_if_already_dead_lettered():
    """Mirrors record_failure's own OR-001 idempotency guarantee: a
    redelivered/duplicate message arriving after this task is already
    dead_lettered must not re-fire the alert or touch retry_count."""
    db = FakeTaskDB()
    dep_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())
    db.seed(dep_id, "ingest-signals", state=TaskStateEnum.DEAD_LETTERED.value)
    db.seed(task_id, "qa-review", state=TaskStateEnum.DEAD_LETTERED.value, depends_on=[dep_id])

    bus = InMemoryServiceBus()
    result = state_machine.cascade_dead_letter(task_id, db, dep_id, producer, bus)

    assert result == TaskStateEnum.DEAD_LETTERED
    assert db.get_task(task_id)["retry_count"] == 0


def test_multiple_dependencies_any_one_dead_lettered_is_enough():
    """depends_on can list more than one entry (a fan-in task). If ANY
    one of them is permanently dead-lettered, the fan-in task can never
    satisfy "every dependency completed" -- it must cascade-fail even
    though its other dependency is still healthily in flight."""
    db = FakeTaskDB()
    dep_ok = str(uuid.uuid4())
    dep_dead = str(uuid.uuid4())
    task_id = str(uuid.uuid4())
    db.seed(dep_ok, "score-signals", state="running")
    db.seed(dep_dead, "ingest-signals", state=TaskStateEnum.DEAD_LETTERED.value)
    db.seed(task_id, "dedupe-signal-cards", state="pending", depends_on=[dep_ok, dep_dead])

    with pytest.raises(dispatch.DependencyDeadLetteredError) as exc_info:
        dispatch.dispatch_task(_envelope(task_id, "dedupe-signal-cards"), db)

    assert exc_info.value.blocking_task_id == dep_dead
