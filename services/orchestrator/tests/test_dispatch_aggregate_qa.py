"""Integration tests for _aggregate_qa_review / qa_review_brand_steward_
handler / qa_review_fact_check_handler.

Zero dedicated coverage existed for this path before this file (confirmed
via `git grep` ahead of PR #97, flagged in that PR's own description
rather than silently left uncovered). This closes that gap, and also
proves end-to-end that PR #97's brand_rules.reconcile_violations backstop
is actually wired into the live handler -- test_brand_rules.py already
covers the pure functions in isolation; this file covers the handler that
calls them, so a future change that quietly un-wires the call site would
be caught here.

Uses tests/fakes.py's FakeVaultClient (via patch_dispatch_clients) for
Vault, and a local FakeTaskDB for the dependency graph / result_ref /
state storage -- same style as test_dispatch.py and test_dispatch_gate.py.
No live Postgres, no live model-gateway.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
from orchestrator import dispatch
from orchestrator.models import TaskEnvelope, TaskStateEnum, TransitionReason


class FakeTaskDB:
    """In-memory stand-in for orchestrator.db's task_state surface --
    covers everything _aggregate_qa_review touches: get_task/get_tasks for
    dependency lookup, set_result_ref/transition/advance_dependents for
    the terminal-state write."""

    def __init__(self) -> None:
        self.tasks: dict[str, dict[str, Any]] = {}

    def seed(
        self,
        task_id: str,
        task_type: str,
        *,
        depends_on: list[str] | None = None,
        result_ref: dict[str, Any] | None = None,
    ) -> None:
        self.tasks[task_id] = {
            "task_id": task_id,
            "task_type": task_type,
            "state": "dispatchable",
            "depends_on": depends_on or [],
            "result_ref": result_ref,
        }

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        return self.tasks.get(task_id)

    def get_tasks(self, task_ids: list[str]) -> list[dict[str, Any]]:
        return [self.tasks[t] for t in task_ids if t in self.tasks]

    def set_result_ref(self, task_id: str, result_ref: dict[str, Any]) -> None:
        self.tasks[task_id]["result_ref"] = result_ref

    def get_result_ref(self, task_id: str) -> dict[str, Any] | None:
        return self.tasks[task_id]["result_ref"]

    def transition(self, task_id: str, to_state, reason) -> None:
        to_state_val = to_state.value if hasattr(to_state, "value") else to_state
        reason_val = reason.value if hasattr(reason, "value") else reason
        self.tasks[task_id]["state"] = to_state_val
        self.tasks[task_id]["_last_reason"] = reason_val

    def advance_dependents(self, completed_task_id: str) -> list[str]:
        return []


class _FixedVerdictGatewayClient:
    """Returns the SAME verdict for every call regardless of draft
    content -- simulates the QA model asserting a specific violation
    (including a hallucinated one), so a test can prove
    _aggregate_qa_review's brand_rules.reconcile_violations call actually
    reconciles it against the real draft text rather than trusting the
    model verbatim."""

    def __init__(self, violations: list[str]) -> None:
        self._violations = violations

    def __enter__(self) -> "_FixedVerdictGatewayClient":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        pass

    def complete(
        self, *, model: str, system_prompt: str, user_content: str, agent_run_id: str, **_kw: Any
    ) -> dict[str, Any]:
        return {
            "id": f"fixed-{uuid.uuid4()}",
            "model": model,
            "content": json.dumps({"pass": not self._violations, "violations": self._violations}),
            "usage": {"input_tokens": 10, "output_tokens": 10},
            "agent_run_id": agent_run_id,
        }


def _envelope(task_id: str, task_type: str) -> TaskEnvelope:
    return TaskEnvelope(
        task_id=uuid.UUID(task_id),
        task_type=task_type,
        agent_run_id=uuid.uuid4(),
        campaign_id=uuid.uuid4(),
        created_at=datetime.now(timezone.utc),
    )


@pytest.fixture()
def clients(monkeypatch):
    from tests.fakes import patch_dispatch_clients

    vault = patch_dispatch_clients(monkeypatch)
    return vault


def _seed_draft(db, vault, *, task_type: str = "draft-case-study", text: str) -> str:
    """Creates a real Vault asset via the shared FakeVaultClient (so
    _aggregate_qa_review's vault.get_asset(vault_asset_id) call resolves
    for real) and seeds a completed draft task pointing at it -- the same
    shape draft_case_study_handler/etc. leave behind in production."""
    asset = vault.create_asset(
        asset_type="draft",
        agent_run_id=str(uuid.uuid4()),
        campaign_id=str(uuid.uuid4()),
        function_id="47",
        content_bytes=text.encode("utf-8"),
    )
    draft_id = str(uuid.uuid4())
    db.seed(
        draft_id,
        task_type,
        result_ref={"vault_asset_id": asset["id"], "content_hash": asset["content_hash"]},
    )
    return draft_id


CLEAN_SA_TEXT = (
    "Month-end closed 2 days faster across 8 entities in 3 countries after "
    "the Fabric migration. See the case study for the full breakdown."
)


def test_aggregate_qa_review_passes_when_all_drafts_clean(clients):
    db = FakeTaskDB()
    vault = clients
    d1 = _seed_draft(db, vault, text=CLEAN_SA_TEXT)
    d2 = _seed_draft(db, vault, task_type="draft-newsletter", text=CLEAN_SA_TEXT)
    qa_id = str(uuid.uuid4())
    db.seed(qa_id, "qa-review-brand-steward", depends_on=[d1, d2])

    dispatch.qa_review_brand_steward_handler(
        qa_id, _envelope(qa_id, "qa-review-brand-steward"), db
    )

    assert db.get_task(qa_id)["state"] == TaskStateEnum.COMPLETED.value
    ref = db.get_result_ref(qa_id)
    assert ref["pass"] is True
    assert len(ref["per_draft"]) == 2
    assert all(v["pass"] for v in ref["per_draft"])


def test_aggregate_qa_review_blocks_when_any_draft_violates(clients):
    db = FakeTaskDB()
    vault = clients
    bad_text = "Read more: http://example.com/no-utm-params-here"
    d1 = _seed_draft(db, vault, text=CLEAN_SA_TEXT)
    d2 = _seed_draft(db, vault, task_type="draft-newsletter", text=bad_text)
    qa_id = str(uuid.uuid4())
    db.seed(qa_id, "qa-review-brand-steward", depends_on=[d1, d2])

    dispatch.qa_review_brand_steward_handler(
        qa_id, _envelope(qa_id, "qa-review-brand-steward"), db
    )

    assert db.get_task(qa_id)["state"] == TaskStateEnum.FAILED.value
    assert db.get_task(qa_id)["_last_reason"] == TransitionReason.QA_BLOCKED.value
    ref = db.get_result_ref(qa_id)
    assert ref["pass"] is False
    failing = [v for v in ref["per_draft"] if not v["pass"]]
    assert len(failing) == 1
    assert "url-utm" in failing[0]["violations"]


def test_aggregate_qa_review_raises_when_no_reviewable_draft(clients):
    db = FakeTaskDB()
    qa_id = str(uuid.uuid4())
    other_id = str(uuid.uuid4())
    db.seed(other_id, "ingest-signals")  # not in QA_REVIEWED_DRAFT_TASK_TYPES
    db.seed(qa_id, "qa-review-brand-steward", depends_on=[other_id])

    with pytest.raises(dispatch.DispatchError):
        dispatch.qa_review_brand_steward_handler(
            qa_id, _envelope(qa_id, "qa-review-brand-steward"), db
        )


def test_aggregate_qa_review_treats_missing_asset_as_violation(clients):
    # A draft dependency that completed upstream but never produced a
    # reviewable asset (e.g. dead-lettered before ever writing one) --
    # QA_BLOCKED intentionally errs toward blocking rather than skipping.
    db = FakeTaskDB()
    d1 = str(uuid.uuid4())
    db.seed(d1, "draft-case-study", result_ref={})
    qa_id = str(uuid.uuid4())
    db.seed(qa_id, "qa-review-brand-steward", depends_on=[d1])

    dispatch.qa_review_brand_steward_handler(
        qa_id, _envelope(qa_id, "qa-review-brand-steward"), db
    )

    assert db.get_task(qa_id)["state"] == TaskStateEnum.FAILED.value
    ref = db.get_result_ref(qa_id)
    assert ref["pass"] is False
    assert ref["per_draft"][0]["violations"] == ["no_reviewable_asset"]


def test_aggregate_qa_review_drops_hallucinated_sa_spelling_violation(clients, monkeypatch):
    """F-QA-DETERMINISTIC-BACKSTOP (PR #97) end-to-end: the model asserts
    sa-english-spelling on a draft that is genuinely clean SA English --
    _aggregate_qa_review must drop it via brand_rules.reconcile_violations
    and let the draft pass, not block the whole run on a hallucination."""
    db = FakeTaskDB()
    vault = clients
    monkeypatch.setattr(
        dispatch,
        "build_gateway_client",
        lambda: _FixedVerdictGatewayClient(["sa-english-spelling"]),
    )
    d1 = _seed_draft(db, vault, text=CLEAN_SA_TEXT)
    qa_id = str(uuid.uuid4())
    db.seed(qa_id, "qa-review-brand-steward", depends_on=[d1])

    dispatch.qa_review_brand_steward_handler(
        qa_id, _envelope(qa_id, "qa-review-brand-steward"), db
    )

    assert db.get_task(qa_id)["state"] == TaskStateEnum.COMPLETED.value
    ref = db.get_result_ref(qa_id)
    assert ref["pass"] is True
    assert ref["per_draft"][0]["violations"] == []


def test_aggregate_qa_review_keeps_genuine_sa_spelling_violation(clients, monkeypatch):
    """The flip side: if the draft text genuinely DOES contain a US
    spelling variant, the deterministic check agrees with the model and
    the violation must NOT be dropped."""
    db = FakeTaskDB()
    vault = clients
    monkeypatch.setattr(
        dispatch,
        "build_gateway_client",
        lambda: _FixedVerdictGatewayClient(["sa-english-spelling"]),
    )
    us_text = "We help you optimize your reporting stack across every entity."
    d1 = _seed_draft(db, vault, text=us_text)
    qa_id = str(uuid.uuid4())
    db.seed(qa_id, "qa-review-brand-steward", depends_on=[d1])

    dispatch.qa_review_brand_steward_handler(
        qa_id, _envelope(qa_id, "qa-review-brand-steward"), db
    )

    assert db.get_task(qa_id)["state"] == TaskStateEnum.FAILED.value
    ref = db.get_result_ref(qa_id)
    assert ref["pass"] is False
    assert "sa-english-spelling" in ref["per_draft"][0]["violations"]


def test_aggregate_qa_review_drops_hallucinated_unsupported_claim_violation(clients, monkeypatch):
    db = FakeTaskDB()
    vault = clients
    monkeypatch.setattr(
        dispatch,
        "build_gateway_client",
        lambda: _FixedVerdictGatewayClient(["unsupported-claim"]),
    )
    d1 = _seed_draft(db, vault, text=CLEAN_SA_TEXT)
    qa_id = str(uuid.uuid4())
    db.seed(qa_id, "qa-review-brand-steward", depends_on=[d1])

    dispatch.qa_review_brand_steward_handler(
        qa_id, _envelope(qa_id, "qa-review-brand-steward"), db
    )

    assert db.get_task(qa_id)["state"] == TaskStateEnum.COMPLETED.value
    assert db.get_result_ref(qa_id)["per_draft"][0]["violations"] == []


def test_qa_review_fact_check_handler_shares_the_same_reconciliation(clients, monkeypatch):
    """qa_review_fact_check_handler routes through the SAME
    _aggregate_qa_review -- prove the backstop applies there too, not
    just for the brand-steward wrapper."""
    db = FakeTaskDB()
    vault = clients
    monkeypatch.setattr(
        dispatch,
        "build_gateway_client",
        lambda: _FixedVerdictGatewayClient(["sa-english-spelling"]),
    )
    d1 = _seed_draft(db, vault, text=CLEAN_SA_TEXT)
    qa_id = str(uuid.uuid4())
    db.seed(qa_id, "qa-review-fact-check", depends_on=[d1])

    dispatch.qa_review_fact_check_handler(qa_id, _envelope(qa_id, "qa-review-fact-check"), db)

    assert db.get_task(qa_id)["state"] == TaskStateEnum.COMPLETED.value
