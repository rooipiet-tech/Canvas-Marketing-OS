"""task_ref idempotency cache — a retried task never double-spends.

Two requests carrying the same ``task_ref`` must produce one upstream call,
one set of costs rows, and two identical responses. That guarantee has to
cover the whole check -> call provider -> store window, not just the
individual storage accesses: without in-flight coordination, two genuinely
concurrent requests could both observe a cache miss before either finished
calling the provider.

TD-06: ``ca-model-gateway`` autoscales, so that in-flight window has to be
coordinated across replicas, not just within one process. This module now
has two ``Cache`` implementations sharing one ``get_or_compute`` contract:

* ``LocalCache`` — the original single-process implementation (an
  insertion-ordered, capped dict of finished responses, plus an in-flight
  map of asyncio Futures). Correct only within one process; used by tests
  and any explicit single-replica/local-dev wiring, never by the deployed
  multi-replica service.
* ``PostgresCache`` — the production default. A durable ``completions``
  table (``services/model-gateway/migrations/0001_completions_init.sql``)
  replaces ``_completed``, and a session-scoped Postgres advisory lock,
  keyed by ``task_ref``, replaces ``_pending``: Postgres itself blocks a
  second replica's caller for the same ``task_ref`` until the first
  finishes, exactly reproducing ``LocalCache``'s "await the first caller's
  future" semantics but across processes. Different ``task_ref`` values
  hash to (almost certainly) different lock keys, so unrelated task_refs
  never serialize behind each other — same non-goal as the old module-level
  lock, which was held only around dict access and never across compute().

Either way, a failed attempt is never written to the completed store: the
exception propagates to the caller that raised it, and a later retry (by
the same caller, or a waiter that unblocked when the failed attempt's lock
was released) re-evaluates from scratch rather than replaying a stale
failure.

``get_cache()`` is the FastAPI dependency (mirrors ``db.get_repository()``):
production wiring always resolves to ``PostgresCache``, never silently to
``LocalCache`` on a missing ``DATABASE_URL`` — an unconfigured production
gateway must fail loudly, not degrade back to the process-local guarantee
this module exists to close.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from typing import Any, Awaitable, Callable, Protocol

import config

CACHE_TABLE = "completions"


class Cache(Protocol):
    """Everything the completion path needs from the idempotency cache."""

    async def get_or_compute(
        self,
        task_ref: str | None,
        compute: Callable[[], Awaitable[dict]],
    ) -> tuple[dict, bool]: ...


class LocalCache:
    """Process-local ``Cache``: correct for one process only.

    ``_completed`` is bounded: an insertion-ordered map capped at
    ``MAX_ENTRIES``, evicting oldest-first on every write. Without a cap it
    would grow once per distinct task_ref for the life of the process — a
    slow memory exhaustion path inside a container capped at 1Gi
    (infra/modules/gateway.bicep). Evicting an entry only costs a
    re-computation on a very late retry of a very old task_ref, which is the
    correct trade against running the process out of memory. ``_pending``
    needs no cap: an entry exists only while its computation is in flight
    and is popped on both the success and the failure path, so it is
    bounded by concurrent request volume, not by history.
    """

    # Bound on retained completed responses. ~10k entries of a small JSON
    # body is a few tens of MB at worst — comfortably inside the container's
    # memory cap while still covering any realistic retry window. Read as an
    # instance attribute (not captured into a closure) so a test can
    # monkeypatch it without reaching into the map itself.
    MAX_ENTRIES = 10_000

    def __init__(self) -> None:
        self._completed: "OrderedDict[str, dict]" = OrderedDict()
        self._pending: dict[str, "asyncio.Future[dict]"] = {}
        self._lock: asyncio.Lock | None = None
        self._lock_loop: Any = None

    def _get_lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:
            self._lock = asyncio.Lock()
            self._lock_loop = loop
        return self._lock

    def _remember(self, task_ref: str, response: dict) -> None:
        self._completed[task_ref] = response
        self._completed.move_to_end(task_ref)
        while len(self._completed) > self.MAX_ENTRIES:
            self._completed.popitem(last=False)

    def size(self) -> int:
        """Number of retained completed responses (inspection helper)."""
        return len(self._completed)

    def get(self, task_ref: str) -> dict | None:
        """Read a completed cached response (test/inspection helper)."""
        return self._completed.get(task_ref)

    def set(self, task_ref: str, response: dict) -> None:
        """Seed a completed cached response (test helper)."""
        self._remember(task_ref, response)

    def clear(self) -> None:
        """Drop all cache state (test teardown)."""
        self._completed.clear()
        self._pending.clear()

    async def get_or_compute(
        self,
        task_ref: str | None,
        compute: Callable[[], Awaitable[dict]],
    ) -> tuple[dict, bool]:
        if not task_ref:
            return await compute(), False

        lock = self._get_lock()
        async with lock:
            cached = self._completed.get(task_ref)
            if cached is not None:
                return cached, True
            inflight = self._pending.get(task_ref)
            if inflight is None:
                inflight = asyncio.get_running_loop().create_future()
                self._pending[task_ref] = inflight
                owner = True
            else:
                owner = False

        if not owner:
            # Ride the in-flight computation started by the first caller.
            return await inflight, True

        try:
            result = await compute()
        except BaseException as exc:  # noqa: BLE001 - re-raised immediately below
            self._pending.pop(task_ref, None)
            if not inflight.done():
                inflight.set_exception(exc)
            # Keep the loop quiet if nobody is waiting on this future.
            inflight.exception()
            raise
        else:
            self._remember(task_ref, result)
            self._pending.pop(task_ref, None)
            if not inflight.done():
                inflight.set_result(result)
            return result, False


class PostgresCache:
    """Cross-process ``Cache``: a durable table plus a Postgres advisory lock.

    Protocol, run entirely on one borrowed connection so the session-scoped
    advisory lock and its later unlock are guaranteed to be the same
    Postgres session:

      1. Borrow a connection from ``db``'s shared pool (see
         ``db.get_pool`` — one pool per process, shared with the
         Repository's own queries, not a second pool).
      2. ``pg_advisory_lock(hashtextextended(task_ref, 0))`` — blocks until
         no other session (this replica or another) holds the same
         task_ref's lock. A 64-bit hash makes an accidental collision
         between two *different* task_refs astronomically unlikely, and even
         a collision would only cost an unrelated caller a brief wait, never
         a correctness violation: the table lookup right after is still
         keyed on the real ``task_ref`` string.
      3. With the lock held, look up ``completions``. A hit is returned
         immediately (cache_hit=True) and the lock is released — this
         replica never calls ``compute()``.
      4. A miss makes this caller the owner: ``compute()`` is awaited while
         still holding the lock (the connection sits idle server-side
         during that await — no worker thread is blocked on it). On success
         the response is inserted and the lock released. On failure nothing
         is written and the lock is released, so a waiter that was blocked
         on step 2 wakes up, finds no row, and becomes the new owner itself
         (a fresh attempt, not a replay of the same exception — see the
         module docstring).

    Holding one pooled connection for the full duration of every
    task_ref-bearing request (not just genuinely concurrent duplicates) is
    the deliberate trade this design makes for "no new infra dependency"
    (TD-06): it costs pool headroom under load, on the same already-tight
    connection budget TD-12 tracks, in exchange for needing no polling loop
    or stale-claim recovery — a crashed owner's session closes, Postgres
    releases its advisory lock automatically, and the next caller simply
    becomes the new owner.
    """

    def __init__(self, dsn: str | None = None) -> None:
        self._dsn = dsn or config.DATABASE_URL

    def _dsn_or_fail(self) -> str:
        if not self._dsn:
            raise RuntimeError("DATABASE_URL is not configured")
        return self._dsn

    @staticmethod
    def _acquire_and_check(conn: Any, task_ref: str) -> dict | None:
        """Acquire the advisory lock and look up ``task_ref``.

        A hit releases the lock itself before returning — this call is the
        ONLY path a cache hit takes, so if it left the lock held, that
        connection would carry a permanently-orphaned lock back into the
        pool: the next caller for the same task_ref that happens to land on
        a *different* pooled connection would then block on
        ``pg_advisory_lock`` forever, since nothing would ever unlock it. A
        miss deliberately leaves the lock held — the caller is now the
        owner and releases it itself, in ``_store_and_release`` or
        ``_release_without_storing``, after ``compute()``.
        """
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT pg_advisory_lock(hashtextextended(%s, 0))", (task_ref,)
                )
                cur.execute(
                    f"SELECT response FROM {CACHE_TABLE} WHERE task_ref = %s", (task_ref,)
                )
                row = cur.fetchone()
                if row is not None:
                    cur.execute(
                        "SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (task_ref,)
                    )
                return row[0] if row else None

    @staticmethod
    def _store_and_release(conn: Any, task_ref: str, response: dict) -> None:
        from psycopg2.extras import Json

        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"INSERT INTO {CACHE_TABLE} (task_ref, response) VALUES (%s, %s) "
                    "ON CONFLICT (task_ref) DO NOTHING",
                    (task_ref, Json(response)),
                )
                cur.execute(
                    "SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (task_ref,)
                )

    @staticmethod
    def _release_without_storing(conn: Any, task_ref: str) -> None:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (task_ref,)
                )

    async def get_or_compute(
        self,
        task_ref: str | None,
        compute: Callable[[], Awaitable[dict]],
    ) -> tuple[dict, bool]:
        if not task_ref:
            return await compute(), False

        import anyio
        import db

        pool = db.get_pool(self._dsn_or_fail())
        conn = await anyio.to_thread.run_sync(pool.getconn)
        try:
            cached = await anyio.to_thread.run_sync(
                self._acquire_and_check, conn, task_ref
            )
            if cached is not None:
                return cached, True

            try:
                result = await compute()
            except BaseException:
                await anyio.to_thread.run_sync(
                    self._release_without_storing, conn, task_ref
                )
                raise
            else:
                await anyio.to_thread.run_sync(
                    self._store_and_release, conn, task_ref, result
                )
                return result, False
        finally:
            await anyio.to_thread.run_sync(pool.putconn, conn)


def get_cache() -> Cache:
    """FastAPI dependency. Tests override this with a fresh LocalCache."""
    return PostgresCache()
