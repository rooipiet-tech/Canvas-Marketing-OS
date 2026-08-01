"""orchestrator/run_state.py — GET /runs/{task_ref} (plan step 17; AC-15).

No live infra needed: exercised directly against an in-memory FakeTaskDB
and a monkeypatched GatekeeperClient.
"""

from __future__ import annotations

import uuid
from typing import Any

from orchestrator import run_state
from tests.test_dispatch import FakeTaskDB


def _seed_full_run(db: FakeTaskDB) -> dict[str, str]:
    ids = {name: str(uuid.uuid4()) for name in ("content", "qa", "approval")}
    db.seed(ids["content"], "draft-content")
    db.tasks[ids["content"]]["state"] = "completed"
    db.tasks[ids["content"]]["result_ref"] = {"vault_asset_id": "asset-1", "content_hash": "h1"}

    db.seed(ids["qa"], "qa-review", depends_on=[ids["content"]])
    db.tasks[ids["qa"]]["state"] = "completed"
    db.tasks[ids["qa"]]["result_ref"] = {"pass": True}

    db.seed(ids["approval"], "request-approval", depends_on=[ids["qa"]])
    db.tasks[ids["approval"]]["state"] = "completed"
    db.tasks[ids["approval"]]["result_ref"] = {
        "decision_id": "d1",
        "outcome": "escalated",
        "content_hash": "h1",
        "agent_run_id": "run-1",
        "function_id": "publish.social_post",
    }
    return ids


def test_unknown_task_ref_returns_none():
    db = FakeTaskDB()
    assert run_state.build_run_state("does-not-exist", db) is None


def test_lineage_collects_every_stage(monkeypatch):
    db = FakeTaskDB()
    ids = _seed_full_run(db)

    monkeypatch.setattr(
        run_state, "_approval_decision_status", lambda stages: {"status": "pending"}
    )
    monkeypatch.setattr(run_state, "_span_presence", lambda task_ids: "not_checked")

    result = run_state.build_run_state(ids["approval"], db)
    assert result is not None
    assert result["stage_count"] == 3
    task_types = {s["task_type"] for s in result["stages"]}
    assert task_types == {"draft-content", "qa-review", "request-approval"}


def test_approval_decision_status_queries_gatekeeper(monkeypatch):
    db = FakeTaskDB()
    ids = _seed_full_run(db)

    captured: dict[str, Any] = {}

    class _FakeGatekeeperClient:
        def __init__(self, base_url):
            captured["base_url"] = base_url

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            pass

        def get_approval_status(self, **kwargs):
            captured.update(kwargs)
            return {"status": "approved", "decided_by": "user@example.com", "decided_at": "now"}

    monkeypatch.setattr(run_state, "GatekeeperClient", _FakeGatekeeperClient)
    monkeypatch.setattr(run_state, "resolve_gatekeeper_base_url", lambda: "http://gk.invalid")
    monkeypatch.setattr(run_state, "_span_presence", lambda task_ids: "not_checked")

    result = run_state.build_run_state(ids["approval"], db)
    assert result["approval_decision_status"]["status"] == "approved"
    assert result["approval_decision_status"]["decided_by"] == "user@example.com"
    assert captured["agent_run_id"] == "run-1"
    assert captured["function_id"] == "publish.social_post"
    assert captured["content_hash"] == "h1"


def test_approval_decision_status_is_none_for_a_run_with_no_approval_stage(monkeypatch):
    db = FakeTaskDB()
    content_id = str(uuid.uuid4())
    db.seed(content_id, "draft-content")
    db.tasks[content_id]["state"] = "completed"

    monkeypatch.setattr(run_state, "_span_presence", lambda task_ids: "not_checked")

    result = run_state.build_run_state(content_id, db)
    assert result["approval_decision_status"] is None


def test_approval_decision_status_degrades_gracefully_on_gatekeeper_failure(monkeypatch):
    db = FakeTaskDB()
    ids = _seed_full_run(db)

    def _raise(**kwargs):
        raise RuntimeError("gatekeeper unreachable")

    class _FailingGatekeeperClient:
        def __init__(self, base_url):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            pass

        get_approval_status = staticmethod(_raise)

    monkeypatch.setattr(run_state, "GatekeeperClient", _FailingGatekeeperClient)
    monkeypatch.setattr(run_state, "resolve_gatekeeper_base_url", lambda: "http://gk.invalid")
    monkeypatch.setattr(run_state, "_span_presence", lambda task_ids: "not_checked")

    result = run_state.build_run_state(ids["approval"], db)
    assert result["approval_decision_status"]["status"] == "unknown"
