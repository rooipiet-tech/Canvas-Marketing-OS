"""AC-012: dead-letter fires at exactly the 3rd application-level failure
(not the 1st, 2nd, or 4th) — decoupled from the transport's
maxDeliveryCount=10. AC-013: the dead-letter alert event validates against
contracts/orchestrator/dead-letter-alert.schema.json and contains
metadata-only fields (task_id, task_type, loop_id, failure_count,
dead_lettered_at) — never client/personal-data-shaped fields.

Requires a live Postgres (clean_pg fixture) — skipped locally in this
sandbox, genuinely run by CI's orchestrator-test job.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from jsonschema import Draft202012Validator
from orchestrator import db, state_machine
from orchestrator.models import TaskStateEnum
from orchestrator.servicebus import producer
from orchestrator.servicebus.local_double import InMemoryServiceBus

DEAD_LETTER_SCHEMA_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "contracts"
    / "orchestrator"
    / "dead-letter-alert.schema.json"
)


def _seed_task(pg_url: str) -> str:
    task_id = str(uuid.uuid4())
    db.insert_task_batch(
        [
            {
                "task_id": task_id,
                "loop_id": "daily-signal-loop",
                "task_type": "draft-brief",
                "depends_on": [],
            }
        ],
        database_url=pg_url,
    )
    return task_id


def test_dead_letter_at_third_failure(clean_pg):
    task_id = _seed_task(clean_pg)
    bus = InMemoryServiceBus()

    state_1 = state_machine.record_failure(task_id, db, producer, bus, database_url=clean_pg)
    assert state_1 == TaskStateEnum.RETRY_PENDING
    assert db.get_task(task_id, database_url=clean_pg)["state"] == TaskStateEnum.RETRY_PENDING.value

    state_2 = state_machine.record_failure(task_id, db, producer, bus, database_url=clean_pg)
    assert state_2 == TaskStateEnum.RETRY_PENDING
    assert db.get_task(task_id, database_url=clean_pg)["state"] == TaskStateEnum.RETRY_PENDING.value

    state_3 = state_machine.record_failure(task_id, db, producer, bus, database_url=clean_pg)
    assert state_3 == TaskStateEnum.DEAD_LETTERED
    assert db.get_task(task_id, database_url=clean_pg)["state"] == TaskStateEnum.DEAD_LETTERED.value


def test_record_failure_is_a_noop_on_an_already_completed_task(clean_pg):
    """F-DUPLICATE-TERMINAL-REQUEUE (closes the round-23 finding): a
    duplicate/redelivered message that reaches record_failure for a task
    that's already COMPLETED must not regress it back to RETRY_PENDING.
    Real-Postgres counterpart to test_dispatch_gate.py's FakeTaskDB-based
    version of the same guard."""
    task_id = _seed_task(clean_pg)
    bus = InMemoryServiceBus()
    db.transition(
        task_id,
        TaskStateEnum.COMPLETED,
        state_machine.TransitionReason.COMPLETED,
        database_url=clean_pg,
    )

    result = state_machine.record_failure(task_id, db, producer, bus, database_url=clean_pg)

    assert result == TaskStateEnum.COMPLETED
    row = db.get_task(task_id, database_url=clean_pg)
    assert row["state"] == TaskStateEnum.COMPLETED.value
    assert row["retry_count"] == 0


def test_record_failure_is_a_noop_on_an_already_failed_task(clean_pg):
    """Same guard, for the QA_BLOCKED-style FAILED terminal state."""
    task_id = _seed_task(clean_pg)
    bus = InMemoryServiceBus()
    db.transition(
        task_id,
        TaskStateEnum.FAILED,
        state_machine.TransitionReason.QA_BLOCKED,
        database_url=clean_pg,
    )

    result = state_machine.record_failure(task_id, db, producer, bus, database_url=clean_pg)

    assert result == TaskStateEnum.FAILED
    row = db.get_task(task_id, database_url=clean_pg)
    assert row["state"] == TaskStateEnum.FAILED.value
    assert row["retry_count"] == 0


def test_alert_event_schema_conformant(clean_pg):
    from orchestrator import dead_letter

    task_id = _seed_task(clean_pg)
    bus = InMemoryServiceBus()

    # drive to the 3rd failure to reach a realistic dead-lettered state,
    # then emit the alert directly for schema-conformance inspection.
    for _ in range(3):
        db.increment_retry(task_id, database_url=clean_pg)
    db.transition(
        task_id,
        TaskStateEnum.DEAD_LETTERED,
        state_machine.TransitionReason.DEAD_LETTERED,
        database_url=clean_pg,
    )

    alert_dict = dead_letter.emit_alert(task_id, db, producer, bus, database_url=clean_pg)

    schema = json.loads(DEAD_LETTER_SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(alert_dict)

    assert set(alert_dict.keys()) == {
        "alert_version",
        "task_id",
        "task_type",
        "loop_id",
        "failure_count",
        "dead_lettered_at",
    }
    assert alert_dict["failure_count"] == 3
    assert bus.queue_depth("event") == 1
