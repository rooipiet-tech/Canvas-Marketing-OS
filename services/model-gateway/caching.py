"""task_ref idempotency cache — a retried task never double-spends.

Two requests carrying the same ``task_ref`` must produce one upstream call,
one set of costs rows, and two identical responses. That guarantee has to
cover the whole check -> call provider -> store window, not just the
individual dict accesses: without in-flight coordination, two genuinely
concurrent requests could both observe a cache miss before either finished
calling the provider.

So the state is two maps:

* ``_completed`` — finished response bodies, keyed by task_ref.
* ``_pending``   — in-flight computations as asyncio Futures, keyed by
  task_ref. A second caller for the same task_ref awaits the first caller's
  future instead of invoking the provider again.

The lock is held only while checking/registering entries — never across the
awaited compute() call, so unrelated task_refs never serialize behind each
other. A failed attempt (including a budget hard-breach) is never written to
``_completed``: the exception propagates to every waiter and a later retry
re-evaluates from scratch.

``_completed`` is bounded: it is an insertion-ordered map capped at
``MAX_ENTRIES``, evicting oldest-first on every write. Without a cap it would
grow once per distinct task_ref for the life of the process — a slow memory
exhaustion path inside a container capped at 1Gi (infra/modules/gateway.bicep).
Evicting an entry only costs a re-computation on a very late retry of a very
old task_ref, which is the correct trade against running the process out of
memory. ``_pending`` needs no cap: an entry exists only while its computation
is in flight and is popped on both the success and the failure path, so it is
bounded by concurrent request volume, not by history.

Scope: process-local by design. Multi-replica / cross-process cache
consistency is explicitly out of scope for this build.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from typing import Any, Awaitable, Callable

# Bound on retained completed responses. ~10k entries of a small JSON body is
# a few tens of MB at worst — comfortably inside the container's memory cap
# while still covering any realistic retry window.
MAX_ENTRIES = 10_000

_completed: "OrderedDict[str, dict]" = OrderedDict()
_pending: dict[str, "asyncio.Future[dict]"] = {}

# One lock, created lazily against the running event loop. asyncio primitives
# bind to the first loop that uses them, so rebinding when the loop changes
# keeps the module usable from more than one loop over a process lifetime
# (e.g. a test suite) without ever having two live locks on one loop.
_lock: asyncio.Lock | None = None
_lock_loop: Any = None


def _get_lock() -> asyncio.Lock:
    global _lock, _lock_loop
    loop = asyncio.get_running_loop()
    if _lock is None or _lock_loop is not loop:
        _lock = asyncio.Lock()
        _lock_loop = loop
    return _lock


def _remember(task_ref: str, response: dict) -> None:
    """Store a completed response, then evict oldest-first back to the cap.

    MAX_ENTRIES is read at call time (not captured) so it stays monkeypatchable
    from a test without reaching into the map itself.
    """
    _completed[task_ref] = response
    _completed.move_to_end(task_ref)
    while len(_completed) > MAX_ENTRIES:
        _completed.popitem(last=False)


def size() -> int:
    """Number of retained completed responses (inspection helper)."""
    return len(_completed)


def get(task_ref: str) -> dict | None:
    """Read a completed cached response (test/inspection helper)."""
    return _completed.get(task_ref)


def set(task_ref: str, response: dict) -> None:  # noqa: A001 - deliberate cache API name
    """Seed a completed cached response (test helper)."""
    _remember(task_ref, response)


def clear() -> None:
    """Drop all cache state (test teardown)."""
    _completed.clear()
    _pending.clear()


async def get_or_compute(
    task_ref: str | None,
    compute: Callable[[], Awaitable[dict]],
) -> tuple[dict, bool]:
    """Return ``(response, cache_hit)`` for this task_ref.

    ``task_ref is None`` means no idempotency key was supplied: compute
    every time, cache nothing.
    """
    if not task_ref:
        return await compute(), False

    lock = _get_lock()
    async with lock:
        cached = _completed.get(task_ref)
        if cached is not None:
            return cached, True
        inflight = _pending.get(task_ref)
        if inflight is None:
            inflight = asyncio.get_running_loop().create_future()
            _pending[task_ref] = inflight
            owner = True
        else:
            owner = False

    if not owner:
        # Ride the in-flight computation started by the first caller.
        return await inflight, True

    try:
        result = await compute()
    except BaseException as exc:  # noqa: BLE001 - re-raised immediately below
        _pending.pop(task_ref, None)
        if not inflight.done():
            inflight.set_exception(exc)
        # Keep the loop quiet if nobody is waiting on this future.
        inflight.exception()
        raise
    else:
        _remember(task_ref, result)
        _pending.pop(task_ref, None)
        if not inflight.done():
            inflight.set_result(result)
        return result, False
