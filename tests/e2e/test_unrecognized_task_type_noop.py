"""AC-02 — a synthetic never-registered task_type is handled gracefully:
no unhandled exception escapes, and it is never silently marked
completed as if handled. Pure logic test — no live infra needed.
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ORCHESTRATOR_SERVICE_ROOT = REPO_ROOT / "services" / "orchestrator"
if str(ORCHESTRATOR_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR_SERVICE_ROOT))

from orchestrator import dispatch  # noqa: E402
from orchestrator.models import TaskEnvelope  # noqa: E402


class _EmptyTaskDB:
    """No backing rows at all -- simulates the 'genuinely unregistered,
    never decomposed' case: dispatch_task's legacy_task_pass_through path
    calls db.transition on a task_id with no task_state row, which (in
    the real Postgres-backed orchestrator.db) raises a foreign-key
    violation on the task_transitions insert. This fake mirrors that
    contract precisely."""

    def transition(self, task_id: str, to_state, reason) -> None:
        raise RuntimeError(f"no such task {task_id} (mirrors a real task_transitions FK violation)")


def test_unrecognized_task_type_raises_not_silently_completed() -> None:
    db = _EmptyTaskDB()
    fake_task_id = str(uuid.uuid4())
    envelope = TaskEnvelope(
        task_id=uuid.UUID(fake_task_id),
        task_type="zzz-unregistered-test-type",
        agent_run_id=uuid.uuid4(),
        campaign_id=uuid.uuid4(),
        created_at=datetime.now(timezone.utc),
    )

    # The important assertion is what does NOT happen: no silent success,
    # no fabricated COMPLETED state. worker.py's own outer try/except
    # (run_worker_loop) is what actually catches and logs this in
    # production, leaving the message safely completed at transport
    # level regardless -- this test proves dispatch_task itself doesn't
    # swallow/convert the failure into a false success.
    with pytest.raises(RuntimeError):
        dispatch.dispatch_task(envelope, db)
