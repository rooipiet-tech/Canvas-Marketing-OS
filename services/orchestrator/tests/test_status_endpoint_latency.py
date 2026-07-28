"""AC-019: the status endpoint reflects a task's state transition within 2
seconds of that transition. Exercised via FastAPI's TestClient (in-process,
same synchronous-DB-read path C13 requires): performs a db.transition()
call, immediately issues GET /status, asserts (a) the transition is
reflected and (b) elapsed wall time < 2.0s.

Requires a live Postgres reachable via DATABASE_URL (main.py's /status
reads via orchestrator.config.DATABASE_URL, not an injected override) —
skipped locally in this sandbox, genuinely run by CI's orchestrator-test
job, which sets both PG_URL and DATABASE_URL to the same value.
"""

from __future__ import annotations

import time
import uuid

import pytest
from orchestrator import db
from orchestrator.models import TaskStateEnum, TransitionReason


def test_status_reflects_transition_within_2s(clean_pg):
    import os

    if not os.environ.get("DATABASE_URL"):
        pytest.skip(
            "DATABASE_URL not set — main.py's /status reads via config.DATABASE_URL directly"
        )

    from fastapi.testclient import TestClient
    from main import app

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
        database_url=clean_pg,
    )

    with TestClient(app) as client:
        start = time.monotonic()
        db.transition(
            task_id, TaskStateEnum.RUNNING, TransitionReason.DISPATCHED, database_url=clean_pg
        )
        response = client.get("/status")
        elapsed = time.monotonic() - start

        assert response.status_code == 200
        body = response.json()
        matching = [t for t in body if t["task_id"] == task_id]
        assert len(matching) == 1
        assert matching[0]["state"] == TaskStateEnum.RUNNING.value
        assert elapsed < 2.0
