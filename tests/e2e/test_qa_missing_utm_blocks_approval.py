"""AC-05 — a deliberately seeded missing-UTM QA violation is caught by
function 02 (Brand Steward QA) via a REAL model-gateway call, and blocks
task progression before the approval stage.

Seeds a real Vault draft-content asset whose body deliberately omits
utm_source/utm_medium/utm_campaign, then invokes
orchestrator.dispatch.qa_review_handler directly (against the REAL live
gateway/Vault, via an in-memory FakeTaskDB standing in only for
orchestrator's own task_state bookkeeping) — proving the block fires
without needing to trigger and wait on a full heartbeat-triggered loop
run just for this one deliberately-bad case.
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

BAD_ASSET_BODY = (
    b"Finance teams keep hearing a different number for the same question. "
    b"Consolidation at scale: a governed lakehouse across many ERPs. "
    b"Read more (no tracking params at all): https://www.canvasintelligence.com/insights\n"
    b"Your Data. Delivered."
)


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
        self.tasks[task_id]["state"] = to_state.value if hasattr(to_state, "value") else to_state

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


def test_seeded_missing_utm_asset_blocks_qa_review(env_vars_configured: dict[str, str]) -> None:
    vault = dispatch.build_vault_client()
    with vault:
        campaign_id = vault.get_or_create_campaign(f"e2e-utm-test-{uuid.uuid4()}")
        agent_run = vault.create_agent_run(
            agent_name="e2e-seed", campaign_id=campaign_id, function_id="42-linkedin-post-writer"
        )
        asset = vault.create_asset(
            asset_type="linkedin_post",
            agent_run_id=agent_run["id"],
            campaign_id=campaign_id,
            function_id="42-linkedin-post-writer",
            content_bytes=BAD_ASSET_BODY,
        )

    db = _FakeTaskDB()
    content_id = str(uuid.uuid4())
    qa_id = str(uuid.uuid4())
    db.seed(content_id, "draft-content")
    db.tasks[content_id]["state"] = "completed"
    db.tasks[content_id]["result_ref"] = {
        "vault_asset_id": asset["id"],
        "content_hash": asset["content_hash"],
    }
    db.seed(qa_id, "qa-review", depends_on=[content_id])

    dispatch.qa_review_handler(qa_id, _envelope(qa_id, "qa-review"), db)

    result = db.get_task(qa_id)
    assert result["state"] == "failed", (
        f"expected qa-review to block on missing UTM params, got state={result['state']!r}"
    )
    ref = result["result_ref"]
    assert ref["pass"] is False
    assert "url-utm" in ref["violations"], f"expected 'url-utm' violation, got {ref['violations']}"
