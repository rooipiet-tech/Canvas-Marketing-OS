"""AC-014: using the local/emulated Service Bus double, killing a worker
mid-task (simulated crash after receive, before complete) 3 times in a
row results in redelivery after each kill and dead-letter after the 3rd,
with a queryable audit trail (one state-history row per attempt, ending in
dead_lettered). Drives worker.reconcile_redelivered_task directly — the
SAME function run_worker_loop calls in production, not a parallel
reimplementation of its logic. Zero live Azure Service Bus reachability
required.

Sequence: the task's message is received once (delivery_count=1) and
"killed" (crashed, never completed) — that is the 1st kill. Each of the
next 2 receives comes back redelivered (delivery_count > 1); the worker
reconciles it via worker.reconcile_redelivered_task (one application-level
failure per reconciliation), then is killed again. 3 kills total => 3
reconciled failures => dead_lettered after the 3rd.

Requires a live Postgres (clean_pg fixture) — skipped locally in this
sandbox, genuinely run by CI's orchestrator-test job.
"""

from __future__ import annotations

import asyncio
import uuid

from orchestrator import db, worker
from orchestrator.models import TaskStateEnum, TransitionReason
from orchestrator.servicebus import producer
from orchestrator.servicebus.local_double import InMemoryServiceBus


def test_kill_worker_three_times_then_dead_letter(clean_pg):
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
        database_url=clean_pg,
    )

    bus = InMemoryServiceBus(default_lock_duration_s=60.0)
    envelope = {
        "envelope_version": "1",
        "task_id": task_id,
        "task_type": "draft-brief",
        "agent_run_id": str(uuid.uuid4()),
        "campaign_id": str(uuid.uuid4()),
        "created_at": "2026-07-28T06:00:00Z",
        "retry_count": 0,
    }
    producer.publish("task", envelope, bus)

    # Kill 1: initial receive (delivery_count == 1), crash without completing.
    received = bus.receive("task", max_count=10)
    assert len(received) == 1
    msg = received[0]
    assert msg.delivery_count == 1
    bus.simulate_crash(msg)
    bus.advance_clock(120.0)
    bus.expire_locks()

    # Kills 2 and 3: each receive is the redelivery caused by the previous
    # kill. The 3rd reconciliation dead-letters the task (AC-012), so only
    # kills 1 and 2 are followed by another simulated crash to set up the
    # next redelivery — there is no 4th receive to prepare for.
    for kill_num in range(1, 4):
        received = bus.receive("task", max_count=10)
        assert len(received) == 1
        msg = received[0]
        assert msg.delivery_count == kill_num + 1

        asyncio.run(worker.reconcile_redelivered_task(msg, db, producer, bus))

        if kill_num < 3:
            bus.simulate_crash(msg)
            bus.advance_clock(120.0)
            bus.expire_locks()

    task = db.get_task(task_id, database_url=clean_pg)
    assert task["state"] == TaskStateEnum.DEAD_LETTERED.value

    import psycopg

    with psycopg.connect(clean_pg) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT to_state, reason FROM task_transitions
                WHERE task_id = %s::uuid ORDER BY occurred_at
                """,
                (task_id,),
            )
            rows = cur.fetchall()

    assert rows[-1][0] == TaskStateEnum.DEAD_LETTERED.value
    assert rows[-1][1] == TransitionReason.DEAD_LETTERED.value

    failure_reasons = [
        r[1]
        for r in rows
        if r[1]
        in (
            TransitionReason.FAILED_ATTEMPT_1.value,
            TransitionReason.FAILED_ATTEMPT_2.value,
            TransitionReason.DEAD_LETTERED.value,
        )
    ]
    assert len(failure_reasons) == 3
