"""orchestrator/task_review.py — GET /tasks/{task_id}/review
(F-TEAMS-CARD-REVIEW-LINK, 11 Aug 2026).

No live infra needed: exercised directly against an in-memory FakeTaskDB
(tests/test_dispatch.py) and a monkeypatched build_vault_client, same
pattern test_run_state.py uses for build_run_state. `task_review.py` binds
build_vault_client/resolve_lineage_result via `from orchestrator.dispatch
import ...`, so patches must target `task_review.build_vault_client`, not
`dispatch.build_vault_client` -- patching the latter would silently no-op
here (the name was already bound at import time).
"""

from __future__ import annotations

import base64

from orchestrator import task_review
from tests.fakes import FakeVaultClient
from tests.test_dispatch import FakeTaskDB


def _seed_qa_blocked(db: FakeTaskDB, vault: FakeVaultClient) -> dict[str, str]:
    asset = vault.create_asset(
        asset_type="linkedin-post",
        agent_run_id="run-1",
        campaign_id="campaign-1",
        function_id="fn",
        content_bytes=b"the original draft text",
    )

    draft_id = "draft-1"
    qa_id = "qa-1"
    db.seed(draft_id, "draft-content")
    db.tasks[draft_id]["state"] = "completed"
    db.tasks[draft_id]["retry_count"] = 0
    db.tasks[draft_id]["result_ref"] = {"vault_asset_id": asset["id"], "content_hash": "h1"}

    db.seed(qa_id, "qa-review", depends_on=[draft_id])
    db.tasks[qa_id]["state"] = "failed"
    db.tasks[qa_id]["retry_count"] = 0
    db.tasks[qa_id]["result_ref"] = {"pass": False, "violations": ["unsupported-claim"]}

    return {"draft_id": draft_id, "qa_id": qa_id}


def test_unknown_task_id_returns_none():
    db = FakeTaskDB()
    assert task_review.build_task_review("does-not-exist", db) is None


def test_returns_full_draft_text_resolved_via_lineage(monkeypatch):
    db = FakeTaskDB()
    vault = FakeVaultClient()
    ids = _seed_qa_blocked(db, vault)

    monkeypatch.setattr(task_review, "build_vault_client", lambda: vault)

    result = task_review.build_task_review(ids["qa_id"], db)

    assert result is not None
    assert result["task_id"] == ids["qa_id"]
    assert result["task_type"] == "qa-review"
    assert result["state"] == "failed"
    assert result["result_ref"]["violations"] == ["unsupported-claim"]
    assert result["draft_text"] == "the original draft text"
    assert result["draft_error"] is None


def test_resolves_brief_body_when_ancestor_carries_brief_id(monkeypatch):
    db = FakeTaskDB()
    vault = FakeVaultClient()
    brief = vault.create_brief(
        title="t", body_text="the brief body", campaign_id="c", function_id="fn"
    )

    brief_task_id = "brief-1"
    qa_id = "qa-1"
    db.seed(brief_task_id, "draft-brief")
    db.tasks[brief_task_id]["state"] = "completed"
    db.tasks[brief_task_id]["retry_count"] = 0
    db.tasks[brief_task_id]["result_ref"] = {"brief_id": brief["id"]}

    db.seed(qa_id, "qa-review", depends_on=[brief_task_id])
    db.tasks[qa_id]["state"] = "failed"
    db.tasks[qa_id]["retry_count"] = 0
    db.tasks[qa_id]["result_ref"] = {"pass": False, "violations": []}

    monkeypatch.setattr(task_review, "build_vault_client", lambda: vault)

    result = task_review.build_task_review(qa_id, db)
    assert result["draft_text"] == "the brief body"


def test_degrades_gracefully_when_no_ancestor_has_a_result_ref():
    db = FakeTaskDB()
    db.seed("lonely-task", "score-signals")  # never gets a result_ref (legacy pass-through)
    db.tasks["lonely-task"]["retry_count"] = 0

    result = task_review.build_task_review("lonely-task", db)
    assert result is not None
    assert result["draft_text"] is None
    assert result["draft_error"] is not None


def test_degrades_gracefully_on_vault_outage(monkeypatch):
    db = FakeTaskDB()
    vault = FakeVaultClient()
    ids = _seed_qa_blocked(db, vault)

    def _raise():
        raise RuntimeError("simulated Vault connectivity failure")

    monkeypatch.setattr(task_review, "build_vault_client", _raise)

    result = task_review.build_task_review(ids["qa_id"], db)
    assert result is not None
    assert result["draft_text"] is None
    assert "unavailable" in result["draft_error"]


def test_content_is_base64_decoded_correctly(monkeypatch):
    """Sanity check the exact bytes round-trip through the same
    base64-encode-in-fixture/decode-in-code path production code uses."""
    db = FakeTaskDB()
    vault = FakeVaultClient()
    raw = "café — emoji check 🎉".encode("utf-8")
    asset = vault.create_asset(
        asset_type="linkedin-post",
        agent_run_id="run-1",
        campaign_id="campaign-1",
        function_id="fn",
        content_bytes=raw,
    )
    assert asset["content_base64"] == base64.b64encode(raw).decode("ascii")

    draft_id, qa_id = "draft-1", "qa-1"
    db.seed(draft_id, "draft-content")
    db.tasks[draft_id]["retry_count"] = 0
    db.tasks[draft_id]["result_ref"] = {"vault_asset_id": asset["id"]}
    db.seed(qa_id, "qa-review", depends_on=[draft_id])
    db.tasks[qa_id]["retry_count"] = 0
    db.tasks[qa_id]["result_ref"] = {"pass": False, "violations": []}

    monkeypatch.setattr(task_review, "build_vault_client", lambda: vault)

    result = task_review.build_task_review(qa_id, db)
    assert result["draft_text"] == raw.decode("utf-8")
