"""migrations/0005_agent_run_idempotency.sql + db.get_ledgered_agent_run/
record_ledgered_agent_run (TD-07, docs/architecture/09-technical-debt.md).

Skips cleanly with no Postgres per this suite's convention (conftest.py's
clean_pg fixture). Complements tests/test_dispatch_retry.py's end-to-end
proof (a real DISPATCH_TABLE handler retried through worker.py, in-memory
doubles only) with a direct proof of this migration's table and db.py's
two new functions against a real Postgres connection.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

MIGRATION_0005 = (
    Path(__file__).resolve().parent.parent / "migrations" / "0005_agent_run_idempotency.sql"
)


@pytest.fixture()
def migrated_pg(clean_pg: str) -> str:
    import psycopg

    sql = MIGRATION_0005.read_text(encoding="utf-8")
    with psycopg.connect(clean_pg) as conn:
        with conn.cursor() as cur:
            # Applied twice in a row to prove idempotency (mirrors 0001's
            # own AC-015 convention, and test_result_ref.py's identical
            # pattern for 0002).
            cur.execute(sql)
            cur.execute(sql)
        conn.commit()
    return clean_pg


def test_unknown_idempotency_key_returns_none(migrated_pg: str) -> None:
    from orchestrator import db

    assert db.get_ledgered_agent_run(str(uuid.uuid4()), database_url=migrated_pg) is None


def test_ledgered_agent_run_round_trips(migrated_pg: str) -> None:
    from orchestrator import db

    idempotency_key = str(uuid.uuid4())
    task_id = str(uuid.uuid4())
    agent_run_id = str(uuid.uuid4())

    db.record_ledgered_agent_run(idempotency_key, task_id, agent_run_id, database_url=migrated_pg)

    assert db.get_ledgered_agent_run(idempotency_key, database_url=migrated_pg) == agent_run_id


def test_duplicate_record_keeps_the_first_writer(migrated_pg: str) -> None:
    """TD-07's actual guarantee: ON CONFLICT DO NOTHING -- a duplicate
    insert for the SAME idempotency_key (two retries racing, or a
    handler calling create_agent_run_idempotent twice for the same
    ordinal by mistake) must never overwrite the first attempt's real
    agent_run_id with a second one."""
    from orchestrator import db

    idempotency_key = str(uuid.uuid4())
    task_id = str(uuid.uuid4())
    first_agent_run_id = str(uuid.uuid4())
    second_agent_run_id = str(uuid.uuid4())

    db.record_ledgered_agent_run(
        idempotency_key, task_id, first_agent_run_id, database_url=migrated_pg
    )
    db.record_ledgered_agent_run(
        idempotency_key, task_id, second_agent_run_id, database_url=migrated_pg
    )

    assert (
        db.get_ledgered_agent_run(idempotency_key, database_url=migrated_pg)
        == first_agent_run_id
    )


def test_ledger_keys_are_independent_per_idempotency_key(migrated_pg: str) -> None:
    """Two different ordinals for the SAME task_id (e.g. the QA retry
    loop's regen + brand_steward + fact_check calls) must not collide --
    each idempotency_key is its own row."""
    from orchestrator import db

    task_id = str(uuid.uuid4())
    key_1 = str(uuid.uuid5(uuid.UUID(task_id), "agent_run:1"))
    key_2 = str(uuid.uuid5(uuid.UUID(task_id), "agent_run:2"))
    agent_run_1 = str(uuid.uuid4())
    agent_run_2 = str(uuid.uuid4())

    db.record_ledgered_agent_run(key_1, task_id, agent_run_1, database_url=migrated_pg)
    db.record_ledgered_agent_run(key_2, task_id, agent_run_2, database_url=migrated_pg)

    assert db.get_ledgered_agent_run(key_1, database_url=migrated_pg) == agent_run_1
    assert db.get_ledgered_agent_run(key_2, database_url=migrated_pg) == agent_run_2
