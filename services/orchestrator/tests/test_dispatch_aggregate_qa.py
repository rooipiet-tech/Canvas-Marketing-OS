"""Integration tests for _single_draft_qa_review / qa_review_brand_steward_
handler / qa_review_fact_check_handler.

ROUND 34 (docs/content-learnings.md, the "batch-gating" finding): these
handlers used to run _aggregate_qa_review, reviewing all 6 Wednesday
drafts inside ONE task that resolved to a single all-or-nothing terminal
state -- confirmed live the night of 10 Aug 2026 that one draft's
violation dead-lettered every other draft's own Friday publish task too.
Restructured so weekly-content-loop.yaml now has one Thursday review task
per draft per review_kind, each depending on exactly one Wednesday draft;
this file was rewritten alongside that change. The single most important
test below is test_single_draft_qa_review_sibling_isolation -- it is the
regression test for the round-34 fix itself, proving one draft's QA_BLOCK
does not touch a sibling draft's own review task state.

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
    covers everything _single_draft_qa_review touches: get_task/get_tasks
    for lineage resolution, set_result_ref/transition/advance_dependents
    for the terminal-state write."""

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
    _single_draft_qa_review's brand_rules.reconcile_violations call
    actually reconciles it against the real draft text rather than
    trusting the model verbatim."""

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
    _single_draft_qa_review's vault.get_asset(vault_asset_id) call
    resolves for real) and seeds a completed draft task pointing at it --
    the same shape draft_case_study_handler/etc. leave behind in
    production."""
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


def test_single_draft_qa_review_passes_when_draft_clean(clients):
    db = FakeTaskDB()
    vault = clients
    d1 = _seed_draft(db, vault, text=CLEAN_SA_TEXT)
    qa_id = str(uuid.uuid4())
    db.seed(qa_id, "qa-review-brand-steward", depends_on=[d1])

    dispatch.qa_review_brand_steward_handler(
        qa_id, _envelope(qa_id, "qa-review-brand-steward"), db
    )

    assert db.get_task(qa_id)["state"] == TaskStateEnum.COMPLETED.value
    ref = db.get_result_ref(qa_id)
    assert ref["pass"] is True
    assert ref["draft_task_id"] == d1
    # content_hash/vault_asset_id forward into this task's own result_ref
    # so schedule_social_buffer_handler / publish_newsletter_handler can
    # resolve them via a plain resolve_lineage_result walk.
    assert ref["content_hash"]
    assert ref["vault_asset_id"]


def test_single_draft_qa_review_blocks_when_draft_violates(clients):
    db = FakeTaskDB()
    vault = clients
    bad_text = "Read more: http://example.com/no-utm-params-here"
    # F-QA-RETRY-LOOP (11 Aug 2026): draft-content-repurpose is the one
    # draft task_type deliberately excluded from the auto-retry loop (see
    # dispatch._DRAFT_REGEN_PARAMS's docstring), so this stays a pure,
    # single-shot test of _single_draft_qa_review's verdict/blocking logic
    # -- unaffected by the new retry mechanics, which get their own
    # dedicated coverage in tests/test_dispatch_qa_retry_loop.py.
    d1 = _seed_draft(db, vault, task_type="draft-content-repurpose", text=bad_text)
    qa_id = str(uuid.uuid4())
    db.seed(qa_id, "qa-review-brand-steward", depends_on=[d1])

    dispatch.qa_review_brand_steward_handler(
        qa_id, _envelope(qa_id, "qa-review-brand-steward"), db
    )

    assert db.get_task(qa_id)["state"] == TaskStateEnum.FAILED.value
    assert db.get_task(qa_id)["_last_reason"] == TransitionReason.QA_BLOCKED.value
    ref = db.get_result_ref(qa_id)
    assert ref["pass"] is False
    assert "url-utm" in ref["violations"]


def test_single_draft_qa_review_sibling_isolation(clients):
    """THE round-34 regression test: one draft's QA_BLOCKED must never
    touch a sibling draft's own review task, even though both were drafted
    in the same weekly run. This is the exact failure confirmed live the
    night of 10 Aug 2026 under the old _aggregate_qa_review -- a single
    bad draft dead-lettered every other draft's Friday publish task too.
    Two entirely separate draft tasks, two entirely separate review tasks
    -- nothing here shares a task_id, mirroring how weekly-content-loop.yaml
    now wires 12 independent Thursday tasks instead of 2 aggregate ones."""
    db = FakeTaskDB()
    vault = clients
    d_clean = _seed_draft(db, vault, task_type="draft-newsletter", text=CLEAN_SA_TEXT)
    # draft-content-repurpose (not draft-carousel-post): keeps this test's
    # scope to sibling-isolation of the single-shot verdict, outside the
    # new F-QA-RETRY-LOOP auto-retry path -- see the comment on
    # test_single_draft_qa_review_blocks_when_draft_violates above.
    d_bad = _seed_draft(
        db, vault, task_type="draft-content-repurpose", text="Read more: http://example.com/no-utm"
    )
    qa_clean = str(uuid.uuid4())
    qa_bad = str(uuid.uuid4())
    db.seed(qa_clean, "qa-review-brand-steward", depends_on=[d_clean])
    db.seed(qa_bad, "qa-review-brand-steward", depends_on=[d_bad])

    dispatch.qa_review_brand_steward_handler(
        qa_bad, _envelope(qa_bad, "qa-review-brand-steward"), db
    )
    dispatch.qa_review_brand_steward_handler(
        qa_clean, _envelope(qa_clean, "qa-review-brand-steward"), db
    )

    assert db.get_task(qa_bad)["state"] == TaskStateEnum.FAILED.value
    assert db.get_task(qa_clean)["state"] == TaskStateEnum.COMPLETED.value
    assert db.get_result_ref(qa_clean)["pass"] is True
    assert db.get_result_ref(qa_clean)["draft_task_id"] == d_clean


def test_single_draft_qa_review_raises_when_no_reviewable_draft(clients):
    db = FakeTaskDB()
    qa_id = str(uuid.uuid4())
    other_id = str(uuid.uuid4())
    # No result_ref at all, and no further depends_on -- resolve_lineage_
    # result's walk finds nothing to review.
    db.seed(other_id, "ingest-signals")
    db.seed(qa_id, "qa-review-brand-steward", depends_on=[other_id])

    with pytest.raises(dispatch.DispatchError):
        dispatch.qa_review_brand_steward_handler(
            qa_id, _envelope(qa_id, "qa-review-brand-steward"), db
        )


def test_single_draft_qa_review_treats_missing_asset_as_violation(clients):
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
    assert ref["violations"] == ["no_reviewable_asset"]


def test_single_draft_qa_review_drops_hallucinated_sa_spelling_violation(clients, monkeypatch):
    """F-QA-DETERMINISTIC-BACKSTOP (PR #97) end-to-end: the model asserts
    sa-english-spelling on a draft that is genuinely clean SA English --
    _single_draft_qa_review must drop it via brand_rules.reconcile_
    violations and let the draft pass, not block it on a hallucination."""
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


def test_single_draft_qa_review_keeps_genuine_sa_spelling_violation(clients, monkeypatch):
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
    # draft-content-repurpose: keeps this test isolated to the fixed-
    # verdict-gateway / reconcile_violations interaction it's actually
    # about, outside the new F-QA-RETRY-LOOP path -- see the comment on
    # test_single_draft_qa_review_blocks_when_draft_violates above.
    d1 = _seed_draft(db, vault, task_type="draft-content-repurpose", text=us_text)
    qa_id = str(uuid.uuid4())
    db.seed(qa_id, "qa-review-brand-steward", depends_on=[d1])

    dispatch.qa_review_brand_steward_handler(
        qa_id, _envelope(qa_id, "qa-review-brand-steward"), db
    )

    assert db.get_task(qa_id)["state"] == TaskStateEnum.FAILED.value
    ref = db.get_result_ref(qa_id)
    assert ref["pass"] is False
    assert "sa-english-spelling" in ref["violations"]
