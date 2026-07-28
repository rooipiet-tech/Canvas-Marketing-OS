"""AC-010: dependency ordering is genuinely enforced. A QA-type task whose
depends_on includes a draft-type task cannot be marked dispatchable while
the draft task's state is anything other than completed; it becomes
dispatchable immediately once the draft task transitions to completed.

Requires a live Postgres (clean_pg fixture) — skipped locally in this
sandbox, genuinely run by CI's orchestrator-test job.
"""

from __future__ import annotations

import uuid

from orchestrator import db
from orchestrator.models import TaskStateEnum, TransitionReason


def test_qa_task_dispatchable_only_after_draft_completes(clean_pg):
    draft_id = str(uuid.uuid4())
    qa_id = str(uuid.uuid4())

    db.insert_task_batch(
        [
            {
                "task_id": draft_id,
                "loop_id": "daily-signal-loop",
                "task_type": "draft-brief",
                "depends_on": [],
            },
            {
                "task_id": qa_id,
                "loop_id": "daily-signal-loop",
                "task_type": "qa-review",
                "depends_on": [draft_id],
            },
        ],
        database_url=clean_pg,
    )

    draft_task = db.get_task(draft_id, database_url=clean_pg)
    qa_task = db.get_task(qa_id, database_url=clean_pg)
    assert draft_task["state"] == TaskStateEnum.DISPATCHABLE.value
    assert qa_task["state"] == TaskStateEnum.PENDING.value

    # qa stays pending while draft is running
    db.transition(
        draft_id, TaskStateEnum.RUNNING, TransitionReason.DISPATCHED, database_url=clean_pg
    )
    qa_task = db.get_task(qa_id, database_url=clean_pg)
    assert qa_task["state"] == TaskStateEnum.PENDING.value

    # completing draft and advancing dependents flips qa to dispatchable
    db.transition(
        draft_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED, database_url=clean_pg
    )
    advanced = db.advance_dependents(draft_id, database_url=clean_pg)
    assert qa_id in advanced

    qa_task = db.get_task(qa_id, database_url=clean_pg)
    assert qa_task["state"] == TaskStateEnum.DISPATCHABLE.value
