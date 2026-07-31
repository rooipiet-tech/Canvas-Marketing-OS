"""AC-016 / AC-016b: the Vault client degrades gracefully when the Vault
API is unreachable — no exception propagates, a WARNING log record is
emitted, the orchestrator's own schema transition still succeeds
independently, and the failed write is COUNTED (not merely logged): a
queryable vault_write_failed_count increments AND a matching
vault_write_failed-reason task_transitions audit row appears.

Requires a live Postgres (clean_pg fixture) — skipped locally in this
sandbox (no Docker/psql), genuinely run by CI's orchestrator-test job
(step 45), consistent with AC-015's own wording. VAULT_API_URL is forced
to an unreachable address (127.0.0.1:1) by conftest.py's session default
AND explicitly here, so this file also proves AC-017 (no live Vault
required) at the unit level.
"""

from __future__ import annotations

import logging

from orchestrator import db, vault_client
from orchestrator.models import TaskStateEnum, TransitionReason

UNREACHABLE_VAULT_URL = "http://127.0.0.1:1"


def _seed_task(pg_url: str) -> str:
    import uuid

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


def test_write_failure_does_not_raise(clean_pg, caplog):
    task_id = _seed_task(clean_pg)

    with caplog.at_level(logging.WARNING, logger="orchestrator.vault_client"):
        vault_client.write_status_best_effort(
            task_id, "running", db, database_url=clean_pg, vault_api_url=UNREACHABLE_VAULT_URL
        )

    assert any(record.levelno == logging.WARNING for record in caplog.records)

    # The orchestrator's own schema transition still succeeds independent
    # of the Vault write's outcome.
    db.transition(
        task_id, TaskStateEnum.RUNNING, TransitionReason.DISPATCHED, database_url=clean_pg
    )
    task = db.get_task(task_id, database_url=clean_pg)
    assert task["state"] == TaskStateEnum.RUNNING.value


def test_failed_write_is_counted(clean_pg):
    task_id = _seed_task(clean_pg)

    vault_client.write_status_best_effort(
        task_id, "running", db, database_url=clean_pg, vault_api_url=UNREACHABLE_VAULT_URL
    )
    task = db.get_task(task_id, database_url=clean_pg)
    assert task["vault_write_failed_count"] == 1

    vault_client.write_status_best_effort(
        task_id, "completed", db, database_url=clean_pg, vault_api_url=UNREACHABLE_VAULT_URL
    )
    task = db.get_task(task_id, database_url=clean_pg)
    assert task["vault_write_failed_count"] == 2

    import psycopg

    with psycopg.connect(clean_pg) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM task_transitions WHERE task_id = %s::uuid AND reason = %s",
                (task_id, TransitionReason.VAULT_WRITE_FAILED.value),
            )
            (count,) = cur.fetchone()
    assert count == 2
