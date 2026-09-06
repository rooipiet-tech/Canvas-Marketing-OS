"""PostgresCache — TD-06's cross-process task_ref idempotency guarantee.

Exercises caching.PostgresCache directly against a real Postgres, proving
what test_caching.py's LocalCache-backed suite cannot: that two separate
connections/sessions (standing in for two ca-model-gateway replicas) racing
on the same task_ref still produce exactly one compute() call, coordinated
purely at the Postgres level via the session-scoped pg_advisory_lock
protocol described in caching.py's PostgresCache docstring.

SKIP-CLEANLY-WHEN-NO-POSTGRES (same convention as
services/gatekeeper/tests/conftest.py, L-0001): if TEST_DATABASE_URL /
DATABASE_URL is unset or unreachable, every test here skips rather than
errors, so a sandbox without Postgres reports skips, not spurious failures.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

import caching  # noqa: E402
from conftest import run  # noqa: E402

MIGRATION_SQL = SERVICE_ROOT / "migrations" / "0001_completions_init.sql"


def _dsn() -> str | None:
    return os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")


@pytest.fixture(scope="session")
def database_url() -> str:
    """Session-scoped: probe Postgres once and apply the migration once."""
    dsn = _dsn()
    if not dsn:
        pytest.skip(
            "TEST_DATABASE_URL is not set — PostgresCache tests need a real "
            "Postgres instance (TD-06, see L-0001's skip-cleanly convention)"
        )

    try:
        import psycopg2
    except ImportError as exc:  # pragma: no cover - environment guard
        pytest.skip(f"psycopg2 is not installed: {exc}")

    try:
        conn = psycopg2.connect(dsn, connect_timeout=5)
    except Exception as exc:  # pragma: no cover - environment guard
        pytest.skip(f"TEST_DATABASE_URL is unreachable ({exc}) — skipping PostgresCache tests")

    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(MIGRATION_SQL.read_text(encoding="utf-8"))
    finally:
        conn.close()
    return dsn


@pytest.fixture
def pg_cache(database_url: str) -> "caching.PostgresCache":
    """A fresh PostgresCache, with the completions table truncated first —
    so "exactly one compute() call" assertions are exact per test."""
    import psycopg2

    conn = psycopg2.connect(database_url)
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE completions")
    finally:
        conn.close()
    return caching.PostgresCache(database_url)


def test_identical_task_ref_across_two_sessions_calls_compute_once(
    pg_cache: "caching.PostgresCache", database_url: str
):
    """Two racing callers on distinct connections/sessions (standing in for
    two ca-model-gateway replicas) for the SAME task_ref still produce
    exactly one compute() call — the cross-process case LocalCache cannot
    prove, closed by the session-scoped advisory lock."""
    calls = 0

    async def compute() -> dict:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.2)
        return {"content": "hello", "call": calls}

    other_cache = caching.PostgresCache(database_url)

    async def scenario():
        return await asyncio.gather(
            pg_cache.get_or_compute("pg-task-race", compute),
            other_cache.get_or_compute("pg-task-race", compute),
        )

    (first_body, first_hit), (second_body, second_hit) = run(scenario())

    assert calls == 1
    assert first_body == second_body
    # Exactly one owner, one waiter — order between two concurrent tasks
    # isn't guaranteed, only that the pair is (owner, waiter).
    assert {first_hit, second_hit} == {True, False}


def test_distinct_task_refs_do_not_serialize_and_both_compute(
    pg_cache: "caching.PostgresCache", database_url: str
):
    calls = 0

    async def compute() -> dict:
        nonlocal calls
        calls += 1
        return {"content": "hello", "call": calls}

    other_cache = caching.PostgresCache(database_url)

    async def scenario():
        return await asyncio.gather(
            pg_cache.get_or_compute("pg-task-a", compute),
            other_cache.get_or_compute("pg-task-b", compute),
        )

    run(scenario())
    assert calls == 2


def test_completed_response_is_durable_across_cache_instances(
    pg_cache: "caching.PostgresCache", database_url: str
):
    """A second, brand-new PostgresCache (a fresh "replica") sees a
    previously-completed response straight from the completions table,
    without ever calling compute() itself."""

    async def compute() -> dict:
        return {"content": "durable"}

    run(pg_cache.get_or_compute("pg-task-durable", compute))

    called = False

    async def should_not_run() -> dict:
        nonlocal called
        called = True
        return {"content": "should not happen"}

    other_cache = caching.PostgresCache(database_url)
    body, cache_hit = run(other_cache.get_or_compute("pg-task-durable", should_not_run))

    assert cache_hit is True
    assert body == {"content": "durable"}
    assert called is False


def test_a_failed_compute_writes_nothing_and_a_retry_recomputes(
    pg_cache: "caching.PostgresCache", database_url: str
):
    """Mirrors LocalCache's guarantee: a failed attempt is never written to
    the completions table, so a later retry re-evaluates from scratch
    rather than replaying a stale failure."""
    attempts = 0

    async def flaky_compute() -> dict:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("boom")
        return {"content": "recovered", "attempt": attempts}

    with pytest.raises(RuntimeError):
        run(pg_cache.get_or_compute("pg-task-flaky", flaky_compute))

    body, cache_hit = run(pg_cache.get_or_compute("pg-task-flaky", flaky_compute))
    assert attempts == 2
    assert cache_hit is False
    assert body == {"content": "recovered", "attempt": 2}


def test_no_task_ref_never_touches_the_table(pg_cache: "caching.PostgresCache", database_url: str):
    async def compute() -> dict:
        return {"content": "uncached"}

    body, cache_hit = run(pg_cache.get_or_compute(None, compute))
    assert cache_hit is False
    assert body == {"content": "uncached"}


def test_a_cache_hit_releases_its_advisory_lock(
    pg_cache: "caching.PostgresCache", database_url: str
):
    """A cache hit must release pg_advisory_lock before returning its
    connection to the pool — otherwise that connection carries a
    permanently-orphaned lock back into the pool, and the next caller for
    the same task_ref that happens to land on a *different* pooled
    connection blocks forever, since nothing would ever unlock it."""

    async def compute() -> dict:
        return {"content": "hit-me"}

    run(pg_cache.get_or_compute("pg-task-hit-unlock", compute))

    # Two more cache hits in a row, each on its own PostgresCache/connection
    # (mirrors two different replicas reading an already-completed
    # task_ref) — every one of them must return promptly, not hang.
    for _ in range(3):
        reader = caching.PostgresCache(database_url)

        async def should_not_run() -> dict:
            raise AssertionError("a cache hit must never call compute()")

        body, cache_hit = run(
            asyncio.wait_for(
                reader.get_or_compute("pg-task-hit-unlock", should_not_run), timeout=5.0
            )
        )
        assert cache_hit is True
        assert body == {"content": "hit-me"}

    # No advisory lock for this task_ref should still be held anywhere.
    import psycopg2

    check_conn = psycopg2.connect(database_url)
    try:
        with check_conn.cursor() as cur:
            cur.execute(
                "SELECT pg_try_advisory_lock(hashtextextended(%s, 0))",
                ("pg-task-hit-unlock",),
            )
            (acquired,) = cur.fetchone()
            assert acquired is True, "the lock is still held — a cache hit leaked it"
            cur.execute(
                "SELECT pg_advisory_unlock(hashtextextended(%s, 0))",
                ("pg-task-hit-unlock",),
            )
    finally:
        check_conn.close()


def test_a_crashed_owners_connection_close_frees_a_waiting_caller(
    pg_cache: "caching.PostgresCache", database_url: str
):
    """A session-scoped advisory lock is released automatically when its
    session/connection closes — even without an explicit unlock — so a
    replica that crashes mid-compute can never permanently strand a
    task_ref for every future caller."""
    import psycopg2

    stuck_conn = psycopg2.connect(database_url)
    with stuck_conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", ("pg-task-crash",))
    # Simulate the owning replica crashing before it can store a response
    # or call pg_advisory_unlock: an abrupt close, not a clean release.
    stuck_conn.close()

    async def compute() -> dict:
        return {"content": "recovered after crash"}

    async def scenario():
        return await asyncio.wait_for(
            pg_cache.get_or_compute("pg-task-crash", compute), timeout=5.0
        )

    body, cache_hit = run(scenario())
    assert cache_hit is False
    assert body == {"content": "recovered after crash"}
