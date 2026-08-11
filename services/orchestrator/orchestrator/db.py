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

import hashlib
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


def set_result_ref(
    task_id: str, result_ref: dict[str, Any], database_url: str | None = None
) -> None:
    """Store a small, structured pointer to a task's downstream artifact
    (migrations/0002_task_result_ref.sql). Never raw content: every value
    must be an id/hash/short-enum string, never free text, matching
    0001's own no-client-data discipline (AC-020/C8). Callers (the 5
    dispatch handlers) are responsible for keeping values short; this
    function only persists the jsonb blob.
    """
    with _connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE task_state SET result_ref = %s::jsonb, updated_at = now() "
                "WHERE task_id = %s::uuid",
                (json.dumps(result_ref), task_id),
            )
        conn.commit()


def get_result_ref(task_id: str, database_url: str | None = None) -> dict[str, Any] | None:
    """Round-trips whatever set_result_ref last stored for task_id, or
    None if never set / task_id unknown."""
    with _connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT result_ref FROM task_state WHERE task_id = %s::uuid",
                (task_id,),
            )
            row = cur.fetchone()
    if row is None or row[0] is None:
        return None
    return row[0] if isinstance(row[0], dict) else json.loads(row[0])


def get_task(task_id: str, database_url: str | None = None) -> dict[str, Any] | None:
    with _connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT task_id, loop_id, task_type, state, retry_count,
                       vault_write_failed_count, depends_on, result_ref
                FROM task_state WHERE task_id = %s::uuid
                """,
                (task_id,),
            )
            row = cur.fetchone()
    if row is None:
        return None
    task_id_, loop_id, task_type, state, retry_count, vault_failed, depends_on, result_ref = row
    return {
        "task_id": str(task_id_),
        "loop_id": loop_id,
        "task_type": task_type,
        "state": state,
        "retry_count": retry_count,
        "vault_write_failed_count": vault_failed,
        "depends_on": depends_on if isinstance(depends_on, list) else json.loads(depends_on),
        "result_ref": (
            result_ref
            if (result_ref is None or isinstance(result_ref, dict))
            else json.loads(result_ref)
        ),
    }


def get_tasks(task_ids: list[str], database_url: str | None = None) -> list[dict[str, Any]]:
    """Batched lookup used by dispatch.py's depends_on-lineage resolution
    (one round trip, never an N+1 per-predecessor loop)."""
    if not task_ids:
        return []
    with _connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT task_id, loop_id, task_type, state, retry_count,
                       vault_write_failed_count, depends_on, result_ref
                FROM task_state WHERE task_id = ANY(%s::uuid[])
                """,
                (task_ids,),
            )
            rows = cur.fetchall()
    results = []
    for row in rows:
        task_id_, loop_id, task_type, state, retry_count, vault_failed, depends_on, result_ref = row
        results.append(
            {
                "task_id": str(task_id_),
                "loop_id": loop_id,
                "task_type": task_type,
                "state": state,
                "retry_count": retry_count,
                "vault_write_failed_count": vault_failed,
                "depends_on": (
                    depends_on if isinstance(depends_on, list) else json.loads(depends_on)
                ),
                "result_ref": (
                    result_ref
                    if (result_ref is None or isinstance(result_ref, dict))
                    else json.loads(result_ref)
                ),
            }
        )
    return results


def find_dependent_tasks(
    depended_on_task_id: str, database_url: str | None = None
) -> list[dict[str, Any]]:
    """Returns every task (ANY state, read-only) whose depends_on jsonb
    array contains depended_on_task_id.

    F-QA-RETRY-LOOP (11 Aug 2026): used by the QA retry loop's sibling-
    task coordination to find a Wednesday draft's two Thursday per-draft
    review tasks (qa-review-brand-steward / qa-review-fact-check) from
    the draft's own task_id. Structurally the same query advance_
    dependents already runs internally, minus the `state = PENDING`
    filter and the COMPLETED-transition side effect -- this is read-only
    and state-agnostic on purpose, since the retry loop needs to find its
    sibling review task regardless of whether that sibling has already
    passed, already failed, or hasn't run yet."""
    with _connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT task_id, loop_id, task_type, state, retry_count,
                       vault_write_failed_count, depends_on, result_ref
                FROM task_state WHERE depends_on @> %s::jsonb
                """,
                (json.dumps([depended_on_task_id]),),
            )
            rows = cur.fetchall()
    results = []
    for row in rows:
        task_id_, loop_id, task_type, state, retry_count, vault_failed, depends_on, result_ref = row
        results.append(
            {
                "task_id": str(task_id_),
                "loop_id": loop_id,
                "task_type": task_type,
                "state": state,
                "retry_count": retry_count,
                "vault_write_failed_count": vault_failed,
                "depends_on": (
                    depends_on if isinstance(depends_on, list) else json.loads(depends_on)
                ),
                "result_ref": (
                    result_ref
                    if (result_ref is None or isinstance(result_ref, dict))
                    else json.loads(result_ref)
                ),
            }
        )
    return results


def advisory_lock_key_for(text: str) -> int:
    """Deterministic signed-int64 key for pg_try_advisory_lock, derived
    from a stable SHA-256 hash of `text` (NOT Python's builtin hash() --
    that's salted per-process by hash randomization, so two different
    orchestrator replicas hashing the same draft_task_id would get two
    different lock keys and the mutual-exclusion this exists for would
    silently not work)."""
    digest = hashlib.sha256(text.encode("utf-8")).digest()[:8]
    value = int.from_bytes(digest, "big", signed=False)
    return value - (1 << 63)  # fold the unsigned 64-bit hash into bigint's signed range


def try_advisory_lock(lock_key: int, database_url: str | None = None) -> psycopg.Connection | None:
    """F-QA-RETRY-LOOP (11 Aug 2026): SESSION-level Postgres advisory lock
    -- the mutual-exclusion primitive for the QA retry loop. A draft's two
    independent Thursday review tasks (brand_steward / fact_check) can
    both fail at nearly the same moment; whichever one calls this first
    and gets a real connection back is the retry-loop OWNER for that
    draft and is the only one that regenerates content or finalizes
    either sibling's terminal state (see dispatch._run_qa_retry_loop).
    The other one gets None back and falls through to its own pre-
    existing single-shot failure behaviour immediately -- no blocking
    wait inside a task handler (see that function's docstring for why
    that tradeoff was chosen over a coordinated wait).

    Returns an OPEN connection holding the lock (caller must eventually
    call release_advisory_lock(conn) exactly once) or None if another
    session already holds it. Session-scoped: if the owner's process
    crashes mid-retry, its connection drops and Postgres releases the
    lock automatically -- a crashed owner can never deadlock a draft."""
    conn = psycopg.connect(database_url or config.DATABASE_URL)
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", (lock_key,))
        (acquired,) = cur.fetchone()
    conn.commit()
    if acquired:
        return conn
    conn.close()
    return None


def release_advisory_lock(conn: psycopg.Connection) -> None:
    """Releases every advisory lock held by `conn`'s session (in practice
    always exactly the one try_advisory_lock acquired on it) and closes
    the connection. Always call from a finally: block around the owned
    retry-loop work."""
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock_all()")
        conn.commit()
    finally:
        conn.close()


def fetch_all_task_status(database_url: str | None = None) -> list[dict[str, Any]]:
    """Used by GET /status (AC-018/AC-019): reads synchronously from this
    schema on every call, no cache, no async projection.

    Exactly 2 queries regardless of task count (F-PERF-002 fix): one for
    the task list, one batched `WHERE task_id = ANY(%s)` query for every
    task's transition history, grouped by task_id in Python — not a
    per-task round trip (the previous N+1 pattern).
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

            task_ids = [str(row[0]) for row in tasks]
            history_by_task: dict[str, list[dict[str, Any]]] = {tid: [] for tid in task_ids}
            if task_ids:
                cur.execute(
                    """
                    SELECT task_id, from_state, to_state, reason, occurred_at
                    FROM task_transitions
                    WHERE task_id = ANY(%s::uuid[])
                    ORDER BY task_id, occurred_at
                    """,
                    (task_ids,),
                )
                for task_id, from_state, to_state, reason, occurred_at in cur.fetchall():
                    history_by_task[str(task_id)].append(
                        {
                            "from_state": from_state,
                            "to_state": to_state,
                            "reason": reason,
                            "occurred_at": occurred_at.isoformat() if occurred_at else None,
                        }
                    )

            result: list[dict[str, Any]] = [
                {
                    "task_id": str(task_id),
                    "loop_id": loop_id,
                    "task_type": task_type,
                    "state": state,
                    "retry_count": retry_count,
                    "vault_write_failed_count": vault_failed,
                    "state_history": history_by_task[str(task_id)],
                }
                for task_id, loop_id, task_type, state, retry_count, vault_failed in tasks
            ]
    return result
