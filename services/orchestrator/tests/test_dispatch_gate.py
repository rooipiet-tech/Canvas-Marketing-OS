"""F-DISPATCH-GATE: dispatch_task must not run a task's handler before the
task itself has actually reached the dispatchable state, and a task
message that arrives too early must be requeued rather than run early or
silently lost.

Root-cause context: worker.handle_heartbeat_message publishes every task
in a decomposed batch onto the `task` queue up front, at heartbeat-
decompose time -- not gated on any earlier stage actually completing.
With more than one orchestrator replica racing the same queue (or simply
an out-of-order redelivery), a downstream task's message can reach
dispatch_task before its predecessor's own message has been handled.
Previously dispatch_task ran unconditionally regardless of the task's real
DB state, so a downstream task could be forced to RUNNING and fail
(no ancestor result_ref yet) -- and since run_worker_loop's task-message
loop unconditionally completes the message even after a handler
exception, that task was then stuck at RUNNING forever with no retry.

Uses tests/fakes-style in-memory doubles (no live Postgres/Service Bus)
so this runs everywhere, including this sandbox.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
from orchestrator import dispatch, worker
from orchestrator.models import TaskEnvelope, TaskStateEnum
from orchestrator.servicebus import producer
from orchestrator.servicebus.local_double import InMemoryServiceBus


class FakeTaskDB:
    """In-memory stand-in for orchestrator.db's task_state surface --
    covers everything dispatch_task/legacy_task_pass_through/worker.py's
    not-ready path and state_machine.record_failure touch."""

    def __init__(self) -> None:
        self.tasks: dict[str, dict[str, Any]] = {}

    def seed(self, task_id: str, task_type: str, *, state: str = "dispatchable") -> None:
        self.tasks[task_id] = {
            "task_id": task_id,
            "task_type": task_type,
            "state": state,
            "depends_on": [],
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


def test_dispatch_task_refuses_a_task_still_pending():
    """A task whose dependency hasn't completed yet (still 'pending', not
    'dispatchable') must raise TaskNotReadyError -- and must NOT be forced
    to RUNNING or have its handler invoked."""
    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    db.seed(task_id, "draft-brief", state="pending")

    with pytest.raises(dispatch.TaskNotReadyError):
        dispatch.dispatch_task(_envelope(task_id, "draft-brief"), db)

    # State must be untouched -- never forced to running.
    assert db.get_task(task_id)["state"] == "pending"


def test_dispatch_task_refuses_unknown_task():
    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    with pytest.raises(dispatch.TaskNotReadyError):
        dispatch.dispatch_task(_envelope(task_id, "draft-brief"), db)


def test_dispatch_task_legacy_pass_through_runs_when_dispatchable():
    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    db.seed(task_id, "score-signals", state="dispatchable")

    dispatch.dispatch_task(_envelope(task_id, "score-signals"), db)

    assert db.get_task(task_id)["state"] == TaskStateEnum.COMPLETED.value


def test_handle_task_message_requeues_a_not_ready_task_instead_of_losing_it():
    """The core regression this fix targets: previously a not-ready task's
    message either ran early (wrong data) or, on handler failure, was
    unconditionally completed by run_worker_loop's finally block and never
    seen again. Now handle_task_message must catch TaskNotReadyError and
    put a fresh copy of the SAME envelope back on the `task` queue."""
    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    db.seed(task_id, "draft-brief", state="pending")  # predecessor not done yet

    bus = InMemoryServiceBus()
    envelope = _envelope(task_id, "draft-brief")

    asyncio.run(worker.handle_task_message(envelope.to_wire_dict(), db, producer, bus))

    # Task itself was never touched.
    assert db.get_task(task_id)["state"] == "pending"
    # A bounced copy of the envelope is back on the queue, one requeue
    # attempt further along, ready to be tried again on a later poll pass.
    assert bus.queue_depth("task") == 1
    requeued = bus.receive("task", max_count=1)[0]
    assert requeued.body["task_id"] == task_id
    assert requeued.body["retry_count"] == 1


def test_handle_task_message_gives_up_after_max_requeues():
    """If a task never becomes dispatchable (dependency permanently stuck),
    handle_task_message must stop requeuing forever and instead route it
    through the real retry/dead-letter state machine."""
    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    db.seed(task_id, "draft-brief", state="pending")

    bus = InMemoryServiceBus()
    envelope = _envelope(task_id, "draft-brief", retry_count=worker.NOT_READY_MAX_REQUEUES)

    asyncio.run(worker.handle_task_message(envelope.to_wire_dict(), db, producer, bus))

    # No infinite requeue -- nothing new was published.
    assert bus.queue_depth("task") == 0
    # Routed into the real failure-handling path instead (state_machine
    # .record_failure's first-failure transition).
    assert db.get_task(task_id)["state"] == TaskStateEnum.RETRY_PENDING.value


def test_handle_task_message_dispatches_normally_once_ready():
    """Sanity check: once a task is genuinely dispatchable, handle_task_message
    behaves exactly as before -- runs it, no requeue."""
    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    db.seed(task_id, "score-signals", state="dispatchable")

    bus = InMemoryServiceBus()
    envelope = _envelope(task_id, "score-signals")

    asyncio.run(worker.handle_task_message(envelope.to_wire_dict(), db, producer, bus))

    assert db.get_task(task_id)["state"] == TaskStateEnum.COMPLETED.value
    assert bus.queue_depth("task") == 0
