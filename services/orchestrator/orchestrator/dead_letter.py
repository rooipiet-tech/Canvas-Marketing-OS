"""Dead-letter alert emission (AC-012, AC-013).

emit_alert reads the task's row from the orchestrator's own schema, builds
a metadata-only DeadLetterAlert (contracts/orchestrator/dead-letter-alert.schema.json
— never client/personal-data-shaped fields, C8), validates it, and
publishes it onto the `event` queue via producer.publish. Returns the dict
for test inspection.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from orchestrator.models import DeadLetterAlert


def emit_alert(
    task_id: str,
    db: Any,
    producer: Any = None,
    client: Any = None,
    database_url: str | None = None,
) -> dict[str, Any]:
    task = db.get_task(task_id, database_url=database_url)
    if task is None:
        raise RuntimeError(f"cannot emit dead-letter alert: task {task_id} not found")

    alert = DeadLetterAlert(
        task_id=task["task_id"],
        task_type=task["task_type"],
        loop_id=task["loop_id"],
        failure_count=3,
        dead_lettered_at=datetime.now(timezone.utc),
    )
    alert_dict = alert.to_wire_dict()

    if producer is not None and client is not None:
        producer.publish("event", alert_dict, client)

    return alert_dict
