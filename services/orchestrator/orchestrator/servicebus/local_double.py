"""In-memory Service Bus double used for local/emulated verification (C4).

Supports the full send/receive/complete/abandon/crash-then-expire-then-
redeliver cycle exercised by tests/test_kill_worker_redelivery.py and by
worker.run_worker_loop when constructed with use_local_double=True — no
live Azure Service Bus reachability required anywhere in this module.

Uses a fake clock (advance_clock / expire_locks) rather than wall-clock
time so lock-expiry-driven redelivery is deterministic in tests.
"""

from __future__ import annotations

import uuid
from collections import deque
from dataclasses import dataclass
from typing import Any


@dataclass
class _Record:
    id: str
    body: dict[str, Any]
    delivery_count: int = 0
    locked_until: float | None = None


@dataclass
class LockedMessage:
    id: str
    queue: str
    body: dict[str, Any]
    delivery_count: int


class InMemoryServiceBus:
    """A uniform (send/receive/complete/abandon) local double for both the
    `task` and `event` queues, keyed by queue name.
    """

    def __init__(self, default_lock_duration_s: float = 60.0) -> None:
        self._pending: dict[str, deque[_Record]] = {}
        self._in_flight: dict[str, _Record] = {}
        self._in_flight_queue: dict[str, str] = {}
        self._now: float = 0.0
        self.default_lock_duration_s = default_lock_duration_s

    def _queue(self, name: str) -> deque[_Record]:
        return self._pending.setdefault(name, deque())

    def send(self, queue: str, body: dict[str, Any]) -> None:
        record = _Record(id=str(uuid.uuid4()), body=dict(body))
        self._queue(queue).append(record)

    def receive(
        self, queue: str, max_count: int = 10, lock_duration_s: float | None = None
    ) -> list[LockedMessage]:
        results: list[LockedMessage] = []
        q = self._queue(queue)
        for _ in range(max_count):
            if not q:
                break
            record = q.popleft()
            record.delivery_count += 1
            record.locked_until = self._now + (lock_duration_s or self.default_lock_duration_s)
            self._in_flight[record.id] = record
            self._in_flight_queue[record.id] = queue
            results.append(
                LockedMessage(
                    id=record.id,
                    queue=queue,
                    body=dict(record.body),
                    delivery_count=record.delivery_count,
                )
            )
        return results

    def complete(self, msg: LockedMessage) -> None:
        self._in_flight.pop(msg.id, None)
        self._in_flight_queue.pop(msg.id, None)

    def abandon(self, msg: LockedMessage) -> None:
        record = self._in_flight.pop(msg.id, None)
        queue = self._in_flight_queue.pop(msg.id, None)
        if record is not None and queue is not None:
            record.locked_until = None
            self._queue(queue).appendleft(record)

    def simulate_crash(self, msg: LockedMessage) -> None:
        """Neither completes nor abandons — leaves the lock in place until
        it expires naturally via expire_locks(), mirroring a real process
        crash between receive and complete.
        """
        return None

    def advance_clock(self, seconds: float) -> None:
        self._now += seconds

    def expire_locks(self) -> None:
        """Test/harness helper: moves any in-flight message whose lock has
        expired back onto its queue, making it redeliverable with its
        delivery_count already incremented from the prior receive().
        """
        expired_ids = [
            mid
            for mid, record in self._in_flight.items()
            if record.locked_until is not None and record.locked_until <= self._now
        ]
        for mid in expired_ids:
            record = self._in_flight.pop(mid)
            queue = self._in_flight_queue.pop(mid)
            record.locked_until = None
            self._queue(queue).appendleft(record)

    def queue_depth(self, queue: str) -> int:
        return len(self._queue(queue))
