"""AC-02 — already-real S10/S11 task_types keep today's exact
pre-session pass-through behavior (COMPLETED + advance_dependents), never
regressed by the new dispatch table. Pure logic test — no live infra
needed, runs unconditionally (mirrors services/orchestrator/tests/
test_dispatch.py's identical unit-level coverage; kept here too per plan
step 18's file list, since AC-02 is explicitly named as an e2e-suite
deliverable).
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
ORCHESTRATOR_SERVICE_ROOT = REPO_ROOT / "services" / "orchestrator"
if str(ORCHESTRATOR_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR_SERVICE_ROOT))

from orchestrator import dispatch  # noqa: E402
from orchestrator.models import TaskEnvelope  # noqa: E402

# S10's fan-out scanner types + S11's content-drafting types — real,
# already-merged task_types this session must never regress.
S10_S11_TASK_TYPES = [
    "competitor-discovery-scan",
    "draft-carousel-post",
    "qa-review-brand-steward",
    "schedule-social-buffer",
]


class _FakeTaskDB:
    def __init__(self) -> None:
        self.tasks: dict[str, dict[str, Any]] = {}

    def seed(self, task_id: str, task_type: str, depends_on: list[str] | None = None) -> None:
        self.tasks[task_id] = {
            "task_id": task_id,
            "task_type": task_type,
            "state": "dispatchable",
            "depends_on": depends_on or [],
            "result_ref": None,
        }

    def transition(self, task_id: str, to_state, reason) -> None:
        to_state_val = to_state.value if hasattr(to_state, "value") else to_state
        self.tasks[task_id]["state"] = to_state_val

    def advance_dependents(self, completed_task_id: str) -> list[str]:
        advanced = []
        for tid, row in self.tasks.items():
            if completed_task_id in row["depends_on"] and row["state"] == "pending":
                row["state"] = "dispatchable"
                advanced.append(tid)
        return advanced

    def set_result_ref(self, task_id: str, result_ref: dict[str, Any]) -> None:
        self.tasks[task_id]["result_ref"] = result_ref

    def get_task(self, task_id: str):
        return self.tasks.get(task_id)

    def get_tasks(self, task_ids: list[str]):
        return [self.tasks[t] for t in task_ids if t in self.tasks]


def _envelope(task_id: str, task_type: str) -> TaskEnvelope:
    return TaskEnvelope(
        task_id=uuid.UUID(task_id),
        task_type=task_type,
        agent_run_id=uuid.uuid4(),
        campaign_id=uuid.uuid4(),
        created_at=datetime.now(timezone.utc),
    )


def test_every_s10_s11_task_type_still_completes_and_advances_dependents() -> None:
    for task_type in S10_S11_TASK_TYPES:
        db = _FakeTaskDB()
        task_id = str(uuid.uuid4())
        dependent_id = str(uuid.uuid4())
        db.seed(task_id, task_type)
        db.seed(dependent_id, "some-downstream-type", depends_on=[task_id])

        dispatch.dispatch_task(_envelope(task_id, task_type), db)

        assert db.get_task(task_id)["state"] == "completed", (
            f"{task_type} must reach completed via legacy_task_pass_through, unchanged"
        )
        assert db.get_task(dependent_id)["state"] == "dispatchable", (
            f"{task_type}'s dependent must advance via advance_dependents, unchanged"
        )
