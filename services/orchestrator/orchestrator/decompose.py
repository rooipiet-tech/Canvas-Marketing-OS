"""Deterministic loop decomposition (C7, C4).

decompose(loop, heartbeat) turns a LoopDefinition + HeartbeatEvent into an
ordered list of task dicts. Task ids are derived via uuid5, seeded by the
heartbeat's event_id and each loop task's declared task_id — the SAME
heartbeat (same event_id) always produces the SAME task_ids, which is
required both for golden-file determinism (AC-009) and for the live smoke
test's correlation lookup (it independently predicts these ids to poll the
deployed app's /status endpoint for).

Output order matches the loop YAML's file order, which is topologically
valid by construction since orchestrator/loop_loader.py already enforces
acyclicity before decompose() is ever called.
"""

from __future__ import annotations

import uuid
from typing import Any

from orchestrator.models import HeartbeatEvent, LoopDefinition


def _task_uuid(event_id: uuid.UUID, loop_task_id: str) -> uuid.UUID:
    return uuid.uuid5(event_id, loop_task_id)


def decompose(loop: LoopDefinition, heartbeat: HeartbeatEvent) -> list[dict[str, Any]]:
    event_id = heartbeat.event_id if isinstance(heartbeat.event_id, uuid.UUID) else uuid.UUID(
        str(heartbeat.event_id)
    )

    tasks: list[dict[str, Any]] = []
    for loop_task in loop.tasks:
        task_uuid = _task_uuid(event_id, loop_task.task_id)
        depends_on_uuids = [str(_task_uuid(event_id, dep)) for dep in loop_task.depends_on]
        tasks.append(
            {
                "task_id": str(task_uuid),
                "task_type": loop_task.task_type,
                "depends_on": depends_on_uuids,
                "loop_id": loop.loop_id,
                "source_task_id": loop_task.task_id,
                # Carried onto the TaskEnvelope's `metadata` bag (already an
                # allowed, string-only field on the frozen task-envelope
                # contract — see worker.py's handle_heartbeat_message) so
                # dispatch.py's qa-review handler can tell whether THIS
                # invocation belongs to the S8 proof circuit
                # (params.proof_circuit) without any frozen-contract change.
                # Task-metadata only (routing hint), never client/personal
                # data, per contracts/service-bus/spec.md.
                "params": loop_task.params,
            }
        )
    return tasks
