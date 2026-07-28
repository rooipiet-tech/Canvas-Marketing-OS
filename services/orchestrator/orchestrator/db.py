"""Postgres persistence layer (C1, C13, C8).

The orchestrator owns an additive `task_state` + `task_transitions` schema
(services/orchestrator/migrations/0001_orchestrator_init.sql) that is the
sole system of record for retry_count and per-transition state history —
independent of the Vault's best-effort agent_runs write (vault_client.py).

Every function accepts an optional `database_url` override (tests pass
$PG_URL explicitly); otherwise falls back to orchestrator.config.DATABASE_URL.
Raises RuntimeError only when actually called without a configured URL —
importing this module never requires a live DB (needed so main.py's
lifespan and worker.py can degrade gracefully per AC-018).
"""

from __future__ import annotations

import json
from typing import Any

import psycopg

from orchestrator import config
from orchestrator.models import TaskStateEnum, TransitionReason


def _connect(database_url: str | None = None) -> psycopg.Connection:
    # Reads config.DATABASE_URL as a live module attribute (not a name
    # bound at import time) so tests that monkeypatch
    # orchestrator.config.DATABASE_URL at runtime are respected.
    url = database_url or config.DATABASE_URL
    if not url:
        raise RuntimeError("DATABASE_URL is not configured")
    return psycopg.connect(url)


def insert_task_batch(
    tasks: list[dict[str, Any]], database_url: str | None = None
) -> None:
    """Insert a batch of decomposed tasks (orchestrator.decompose.decompose's
    output shape). A task with an empty depends_on list starts
    dispatchable; otherwise pending. Each insert also writes a CREATED
    transitions row. Idempotent per task_id (ON CONFLICT DO NOTHING).
    """
    with _connect(database_url) as conn:
        with conn.cursor() as cur:
            for task in tasks:
                depends_on = task.get("depends_on", [])
                state = (
                    TaskStateEnum.DISPATCHABLE.value
                    if not depends_on
                    else TaskStateEnum.PENDING.value
                )
                cur.execute(
                    """
                    INSERT INTO task_state (task_id, loop_id, task_type, state, depends_on)
                    VALUES (%s::uuid, %s, %s, %s, %s::jsonb)
                    ON CONFLICT (task_id) DO NOTHING
                    """,
                    (
                        task["task_id"],
                        task["loop_id"],
                        task["task_type"],
                        state,
                        json.dumps(depends_on),
                    ),
                )
                if cur.rowcount:
                    cur.execute(
                        """
                        INSERT INTO task_transitions (task_id, from_state, to_state, reason)
                        VALUES (%s::uuid, NULL, %s, %s)
                        """,
                        (task["task_id"], state, TransitionReason.CREATED.value),
                    )
        conn.commit()


def transition(
    task_id: str,
    to_state: TaskStateEnum | str,
    reason: TransitionReason,
    database_url: str | None = None,
) -> None:
    """Writes one task_transitions row and updates task_state.state, in one
    transaction. `reason` is typed TransitionReason (F6) — the DB-level
    CHECK constraint on task_transitions.reason enforces the same closed
    vocabulary as a structural backstop.
    """
    to_state_val = to_state.value if isinstance(to_state, TaskStateEnum) else to_state
    reason_val = reason.value if isinstance(reason, TransitionReason) else reason
    with _connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT state FROM task_state WHERE task_id = %s::uuid FOR UPDATE",
                (task_id,),
            )
            row = cur.fetchone()
            from_state = row[0] if row else None
            cur.execute(
                "UPDATE task_state SET state = %s, updated_at = now() WHERE task_id = %s::uuid",
                (to_state_val, task_id),
            )
            cur.execute(
                """
                INSERT INTO task_transitions (task_id, from_state, to_state, reason)
                VALUES (%s::uuid, %s, %s, %s)
                """,
                (task_id, from_state, to_state_val, reason_val),
            )
        conn.commit()


def advance_dependents(
    completed_task_id: str, database_url: str | None = None
) -> list[str]:
    """AC-010: finds every task whose depends_on jsonb array contains
    completed_task_id; if ALL its deps are now completed, transitions it
    pending -> dispatchable with reason DEPENDENCY_SATISFIED. Returns the
    list of task_ids that were advanced.
    """
    advanced: list[str] = []
    with _connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT task_id, depends_on FROM task_state
                WHERE depends_on @> %s::jsonb AND state = %s
                """,
                (json.dumps([completed_task_id]), TaskStateEnum.PENDING.value),
            )
            candidates = cur.fetchall()
            for task_id, depends_on_json in candidates:
                dep_ids = (
                    depends_on_json
                    if isinstance(depends_on_json, list)
                    else json.loads(depends_on_json)
                )
                if not dep_ids:
                    continue
                cur.execute(
                    """
                    SELECT count(*) FROM task_state
                    WHERE task_id = ANY(%s::uuid[]) AND state = %s
                    """,
                    (dep_ids, TaskStateEnum.COMPLETED.value),
                )
                (completed_count,) = cur.fetchone()
                if completed_count == len(dep_ids):
                    advanced.append(str(task_id))
    for task_id in advanced:
        transition(
            task_id,
            TaskStateEnum.DISPATCHABLE,
            TransitionReason.DEPENDENCY_SATISFIED,
            database_url=database_url,
        )
    return advanced


def increment_retry(task_id: str, database_url: str | None = None) -> int:
    with _connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE task_state SET retry_count = retry_count + 1, updated_at = now()
                WHERE task_id = %s::uuid
                RETURNING retry_count
                """,
                (task_id,),
            )
            row = cur.fetchone()
        conn.commit()
    if row is None:
        raise RuntimeError(f"task {task_id} not found")
    return row[0]


def increment_vault_write_failure(task_id: str, database_url: str | None = None) -> int:
    """AC-016b (F6): in addition to incrementing the counter, also inserts a
    no-op (from_state == to_state) task_transitions audit row with reason
    VAULT_WRITE_FAILED — strengthens the "queryable" requirement beyond a
    bare counter.
    """
    with _connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE task_state
                SET vault_write_failed_count = vault_write_failed_count + 1, updated_at = now()
                WHERE task_id = %s::uuid
                RETURNING vault_write_failed_count, state
                """,
                (task_id,),
            )
            row = cur.fetchone()
            if row is None:
                raise RuntimeError(f"task {task_id} not found")
            new_count, current_state = row
            cur.execute(
                """
                INSERT INTO task_transitions (task_id, from_state, to_state, reason)
                VALUES (%s::uuid, %s, %s, %s)
                """,
                (task_id, current_state, current_state, TransitionReason.VAULT_WRITE_FAILED.value),
            )
        conn.commit()
    return new_count


def get_task(task_id: str, database_url: str | None = None) -> dict[str, Any] | None:
    with _connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT task_id, loop_id, task_type, state, retry_count, vault_write_failed_count
                FROM task_state WHERE task_id = %s::uuid
                """,
                (task_id,),
            )
            row = cur.fetchone()
    if row is None:
        return None
    task_id_, loop_id, task_type, state, retry_count, vault_failed = row
    return {
        "task_id": str(task_id_),
        "loop_id": loop_id,
        "task_type": task_type,
        "state": state,
        "retry_count": retry_count,
        "vault_write_failed_count": vault_failed,
    }


def fetch_all_task_status(database_url: str | None = None) -> list[dict[str, Any]]:
    """Used by GET /status (AC-018/AC-019): reads synchronously from this
    schema on every call, no cache, no async projection.
    """
    with _connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT task_id, loop_id, task_type, state, retry_count, vault_write_failed_count
                FROM task_state ORDER BY created_at
                """
            )
            tasks = cur.fetchall()
            result: list[dict[str, Any]] = []
            for task_id, loop_id, task_type, state, retry_count, vault_failed in tasks:
                cur.execute(
                    """
                    SELECT from_state, to_state, reason, occurred_at
                    FROM task_transitions WHERE task_id = %s::uuid ORDER BY occurred_at
                    """,
                    (str(task_id),),
                )
                history = [
                    {
                        "from_state": from_state,
                        "to_state": to_state,
                        "reason": reason,
                        "occurred_at": occurred_at.isoformat() if occurred_at else None,
                    }
                    for from_state, to_state, reason, occurred_at in cur.fetchall()
                ]
                result.append(
                    {
                        "task_id": str(task_id),
                        "loop_id": loop_id,
                        "task_type": task_type,
                        "state": state,
                        "retry_count": retry_count,
                        "vault_write_failed_count": vault_failed,
                        "state_history": history,
                    }
                )
    return result
