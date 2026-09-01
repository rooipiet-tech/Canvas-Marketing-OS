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


# ---------------------------------------------------------------------
# Process 8 -- measure. The two rows the measurement stage reads and
# nothing in production ever wrote.
# ---------------------------------------------------------------------
#
# analytics.* is the analytics-ingest service's schema, and these are the
# only two writes the orchestrator makes into it. That crossing is
# deliberate and narrow. Both tables are pure lookups whose content is
# knowable at exactly one moment -- when an asset is published -- and the
# orchestrator is the only component that holds it then: the publisher
# receives bytes and a token and never learns the campaign slug, and the
# Vault stores campaign_id as a uuid with the slug nowhere in its schema
# (which is frozen). analytics-ingest remains the only reader, the only
# thing that quarantines, and the only owner of every other table there.
#
# Every service in this deployment connects to the same Postgres database
# (infra/main.bicep's single governanceDatabaseUrl); these are separate
# schemas, not separate databases.


# ---------------------------------------------------------------------
# Process 9 -- report on cost and performance.
# ---------------------------------------------------------------------
#
# Reads only. The month-end report is assembled from what the other nine
# processes actually recorded: costs metered on every model call, the
# nightly KPI rollups, the attribution outcomes, and the publish sweep's
# own result_refs. Nothing here computes a new fact -- a report that
# derives numbers nobody else recorded is a report nobody can check.


def month_costs(start, end, database_url: str | None = None) -> dict[str, Any]:
    """Total spend for the window, and its split by provider and by the
    function that incurred it.

    Grouped by agent_runs.agent_name, not by function_id: agent_runs has
    no function_id column (agent_name is what it carries), and agent_name
    is also the dimension analytics' own rollup_cost_per_accepted_asset
    groups by -- so the report and the nightly KPI speak one vocabulary
    rather than two that cannot be reconciled."""
    with _connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COALESCE(SUM(c.amount), 0)::text, COUNT(*)
                  FROM costs c
                 WHERE c.incurred_at >= %s AND c.incurred_at < %s
                """,
                (start, end),
            )
            total, call_count = cur.fetchone()
            cur.execute(
                """
                SELECT c.provider, COALESCE(SUM(c.amount), 0)::text, COUNT(*)
                  FROM costs c
                 WHERE c.incurred_at >= %s AND c.incurred_at < %s
                 GROUP BY c.provider ORDER BY 2 DESC
                """,
                (start, end),
            )
            by_provider = [
                {"provider": row[0], "amount": row[1], "calls": row[2]} for row in cur.fetchall()
            ]
            cur.execute(
                """
                SELECT r.agent_name, COALESCE(SUM(c.amount), 0)::text, COUNT(*)
                  FROM costs c
                  JOIN agent_runs r ON r.id = c.agent_run_id
                 WHERE c.incurred_at >= %s AND c.incurred_at < %s
                 GROUP BY r.agent_name ORDER BY 2 DESC
                """,
                (start, end),
            )
            by_agent = [
                {"agent_name": row[0], "amount": row[1], "calls": row[2]}
                for row in cur.fetchall()
            ]
    return {
        "total": total,
        "calls": call_count,
        "by_provider": by_provider,
        "by_agent": by_agent,
    }


def month_kpis(start, end, database_url: str | None = None) -> dict[str, Any]:
    """The nightly rollups for the window, aggregated to one month.

    Every one of these is empty until the corresponding join has data --
    which is precisely what the report must be able to say out loud, so
    the empty case is returned as an empty list rather than smoothed into
    a zero that would read as a measured result."""
    with _connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT source, post_archetype,
                       ROUND(AVG(engagement_rate), 6)::text, SUM(post_count)
                  FROM analytics.kpi_rollup_engagement_by_archetype
                 WHERE day >= %s AND day < %s
                 GROUP BY source, post_archetype ORDER BY 4 DESC
                """,
                (start, end),
            )
            engagement = [
                {
                    "source": row[0],
                    "post_archetype": row[1],
                    "engagement_rate": row[2],
                    "posts": row[3],
                }
                for row in cur.fetchall()
            ]
            cur.execute(
                """
                SELECT channel, SUM(scheduled_count), SUM(published_count)
                  FROM analytics.kpi_rollup_publishing_reliability
                 WHERE day >= %s AND day < %s
                 GROUP BY channel ORDER BY 1
                """,
                (start, end),
            )
            reliability = [
                {"channel": row[0], "scheduled": row[1], "published": row[2]}
                for row in cur.fetchall()
            ]
            cur.execute(
                """
                SELECT agent_name, SUM(total_cost_usd)::text, SUM(accepted_asset_count)
                  FROM analytics.kpi_rollup_cost_per_accepted_asset
                 WHERE day >= %s AND day < %s
                 GROUP BY agent_name ORDER BY 2 DESC
                """,
                (start, end),
            )
            cost_per_asset = [
                {"agent_name": row[0], "cost": row[1], "accepted_assets": row[2]}
                for row in cur.fetchall()
            ]
    return {
        "engagement": engagement,
        "reliability": reliability,
        "cost_per_accepted_asset": cost_per_asset,
    }


def month_attribution(start, end, database_url: str | None = None) -> dict[str, Any]:
    """How much of the month's measurement could be attributed at all.

    The single most important number in the report while the pipeline is
    young: a quarantine rate near 100% means every performance figure
    above it is drawn from nothing. Grouped by reason so the answer says
    WHY -- an unregistered campaign and a missing utm parameter are
    different failures with different fixes."""
    with _connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT split_part(reason, ':', 1), COUNT(*)
                  FROM analytics.utm_quarantine
                 WHERE day >= %s AND day < %s
                 GROUP BY 1 ORDER BY 2 DESC
                """,
                (start, end),
            )
            quarantined = [{"reason": row[0], "rows": row[1]} for row in cur.fetchall()]
            cur.execute("SELECT COUNT(*) FROM analytics.utm_campaign_map")
            registered = cur.fetchone()[0]
    return {
        "quarantined_by_reason": quarantined,
        "quarantined_total": sum(item["rows"] for item in quarantined),
        "registered_campaigns": registered,
    }


def month_publishes(start, end, database_url: str | None = None) -> dict[str, Any]:
    """What the publish sweep actually did, from its own result_refs.

    Distinguishes `published` from `published_dry_run`: "no posts went
    out" and "posts went out" are the two things a reader most needs kept
    apart, and while PUBLISHER_DRY_RUN is true every publish is the
    former however healthy the counts look."""
    with _connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COALESCE(result_ref ->> 'publish_status', 'unknown'), COUNT(*)
                  FROM task_state
                 WHERE task_type = ANY(%s)
                   AND result_ref ? 'publish_attempt_id'
                   AND created_at >= %s AND created_at < %s
                 GROUP BY 1 ORDER BY 2 DESC
                """,
                (["schedule-social-buffer", "publish-newsletter"], start, end),
            )
            by_status = [{"status": row[0], "count": row[1]} for row in cur.fetchall()]
    return {
        "by_status": by_status,
        "total": sum(item["count"] for item in by_status),
    }


def register_utm_campaign(
    utm_campaign_slug: str,
    vault_campaign_id: str | None,
    asset_id: str | None,
    database_url: str | None = None,
) -> None:
    """Register a published asset's utm_campaign slug so the nightly
    ingest can attribute metrics carrying it.

    THE DEAD JOIN. analytics_ingest.utm.reconcile_utm looks every ingested
    row's utm_campaign up in analytics.utm_campaign_map and quarantines
    anything it cannot match with `unmatched_utm_campaign`. The only
    INSERT into that table anywhere in the repository was in
    tests/conftest.py, so in production the map was permanently empty and
    100% of ingested Buffer/GA4/Search Console/LinkedIn rows quarantined.
    Measurement could not attribute anything to anything, ever.

    Idempotent by the table's own unique constraint on the slug. A week's
    six assets share one slug by design (that is the point of deriving it
    from the pillar), so the second and later registrations of a week are
    expected no-ops rather than conflicts -- which is also why this does
    not update vault_campaign_id/asset_id on conflict: the first asset
    published under a slug is a fine representative, and silently
    repointing the map at whichever asset happened to publish last would
    make the mapping unstable for no gain.
    """
    if not utm_campaign_slug:
        return
    with _connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO analytics.utm_campaign_map
                    (utm_campaign_slug, vault_campaign_id, asset_id)
                VALUES (%s, %s, %s)
                ON CONFLICT (utm_campaign_slug) DO NOTHING
                """,
                (utm_campaign_slug, vault_campaign_id, asset_id),
            )
        conn.commit()


def record_scheduled_post(channel: str, database_url: str | None = None) -> None:
    """Increment today's scheduled-post count for `channel`.

    THE OTHER DEAD JOIN. rollup_publishing_reliability divides a channel's
    observed published_count by analytics.scheduled_posts.scheduled_count,
    and skips any channel with no row -- so with nothing writing that
    table it produced no rows at all, silently. "Did we publish what we
    said we would" reported nothing rather than reporting a problem, which
    is the worse of the two failures.

    Counts the publish attempt, not the observed result: this is the
    denominator, and the numerator is what the nightly ingest actually
    finds on the channel. Recording both from the same observation would
    make the ratio 1.0 by construction and measure nothing.
    """
    if not channel:
        return
    with _connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO analytics.scheduled_posts (day, channel, scheduled_count)
                VALUES (CURRENT_DATE, %s, 1)
                ON CONFLICT (day, channel) DO UPDATE
                   SET scheduled_count = analytics.scheduled_posts.scheduled_count + 1
                """,
                (channel,),
            )
        conn.commit()


def find_awaiting_publication(
    task_types: list[str], database_url: str | None = None
) -> list[dict[str, Any]]:
    """Completed approval tasks that raised an approval and have not yet
    been published.

    The publish step cannot live in the loop that requested the approval:
    request-approval completes the instant /gate-check responds and never
    waits on the human (its own docstring, AC-01), so by the time anyone
    clicks Approve that loop run is long finished. This is the query the
    separate publish loop runs on its own heartbeat, which is also what
    makes an approval granted three days late still publish.

    Two filters, both in SQL so a large task table stays one round trip:
    the result_ref must carry an `approval_id` (an approval was actually
    raised) and must NOT carry a `publish_attempt_id` (this handler has
    not already published it). The second is belt to the publisher's
    braces -- a gate token is single-use through its JTI ledger, and the
    hash bound into the token must match the bytes -- but it keeps the
    loop from re-attempting a publish it already completed.
    """
    if not task_types:
        return []
    with _connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT task_id, loop_id, task_type, state, retry_count,
                       vault_write_failed_count, depends_on, result_ref
                FROM task_state
                WHERE task_type = ANY(%s)
                  AND state = 'completed'
                  AND result_ref ? 'approval_id'
                  AND NOT (result_ref ? 'publish_attempt_id')
                ORDER BY created_at
                """,
                (task_types,),
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
