"""migrations/0002_task_result_ref.sql + db.set_result_ref/get_result_ref
(plan step 2). Skips cleanly with no Postgres per this suite's convention
(conftest.py's clean_pg fixture)."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

MIGRATION_0002 = (
    Path(__file__).resolve().parent.parent / "migrations" / "0002_task_result_ref.sql"
)


@pytest.fixture()
def migrated_pg(clean_pg: str) -> str:
    import psycopg

    sql = MIGRATION_0002.read_text(encoding="utf-8")
    with psycopg.connect(clean_pg) as conn:
        with conn.cursor() as cur:
            # Applied twice in a row to prove idempotency (mirrors 0001's
            # own AC-015 convention).
            cur.execute(sql)
            cur.execute(sql)
        conn.commit()
    return clean_pg


def _insert_task(pg_url: str, task_id: str, depends_on: list[str] | None = None) -> None:
    import psycopg

    with psycopg.connect(pg_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO task_state (task_id, loop_id, task_type, state, depends_on)
                VALUES (%s::uuid, %s, %s, %s, %s::jsonb)
                ON CONFLICT (task_id) DO NOTHING
                """,
                (task_id, "daily-signal-loop", "ingest-signals", "dispatchable", json.dumps(depends_on or [])),
            )
        conn.commit()


def test_result_ref_round_trips(migrated_pg: str) -> None:
    from orchestrator import db

    task_id = str(uuid.uuid4())
    _insert_task(migrated_pg, task_id)

    assert db.get_result_ref(task_id, database_url=migrated_pg) is None

    ref = {"vault_asset_id": str(uuid.uuid4()), "content_hash": "abc123"}
    db.set_result_ref(task_id, ref, database_url=migrated_pg)
    assert db.get_result_ref(task_id, database_url=migrated_pg) == ref


def test_result_ref_defaults_to_none_and_stays_short(migrated_pg: str) -> None:
    from orchestrator import db

    task_id = str(uuid.uuid4())
    _insert_task(migrated_pg, task_id)
    task = db.get_task(task_id, database_url=migrated_pg)
    assert task is not None
    assert task["result_ref"] is None
    assert task["depends_on"] == []


def test_get_tasks_batched_lookup(migrated_pg: str) -> None:
    from orchestrator import db

    parent_id = str(uuid.uuid4())
    child_id = str(uuid.uuid4())
    _insert_task(migrated_pg, parent_id)
    _insert_task(migrated_pg, child_id, depends_on=[parent_id])
    db.set_result_ref(parent_id, {"vault_asset_id": "a1"}, database_url=migrated_pg)

    rows = db.get_tasks([parent_id, child_id], database_url=migrated_pg)
    by_id = {r["task_id"]: r for r in rows}
    assert by_id[parent_id]["result_ref"] == {"vault_asset_id": "a1"}
    assert by_id[child_id]["depends_on"] == [parent_id]
