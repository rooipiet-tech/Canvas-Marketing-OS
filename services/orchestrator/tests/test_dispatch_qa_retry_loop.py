"""F-QA-RETRY-LOOP (11 Aug 2026): coverage for _run_qa_retry_loop and its
wiring into _single_draft_qa_review's not-passed branch -- the reopened
qa-feedback-loop-proposal-2026-08-05.md redesign (see
claude_qa-feedback-loop-proposal-b-v2-2026-08-11.md for the full spec).

Deliberately a SEPARATE file from test_dispatch_aggregate_qa.py: that file
tests _single_draft_qa_review's verdict/reconciliation logic in isolation
(using draft-content-repurpose, the one draft task_type excluded from this
retry loop, precisely so it stays unaffected by this feature -- see the
comments added there 11 Aug 2026). This file is the retry loop's own
coverage: multi-attempt convergence, retries-exhausted escalation, the
NEVER_RETRYABLE hard stop, and the sibling-task lock-contention fallback.

Uses a purpose-built fake gateway (not tests/fakes.py's generic
FakeGatewayClient, which has no branch for a drafting regeneration call
or for the Fact-Check Verdict prompt) so multi-round convergence can
actually be simulated realistically. No live Postgres, no live
model-gateway.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
from orchestrator import dispatch, teams_notify
from orchestrator.models import TaskEnvelope, TaskStateEnum, TransitionReason


class FakeTaskDB:
    """Same shape as test_dispatch_aggregate_qa.py's FakeTaskDB, plus the
    4 F-QA-RETRY-LOOP coordination methods real orchestrator.db now
    exposes (find_dependent_tasks, advisory_lock_key_for,
    try_advisory_lock, release_advisory_lock). try_advisory_lock always
    succeeds by default (returns a sentinel "connection") -- individual
    tests monkeypatch it to simulate lock contention."""

    def __init__(self) -> None:
        self.tasks: dict[str, dict[str, Any]] = {}
        self.released_locks: list[Any] = []

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
        advanced = []
        for tid, row in self.tasks.items():
            if completed_task_id in row["depends_on"] and row["state"] == "pending":
                all_done = all(
                    self.tasks.get(dep, {}).get("state") == "completed"
                    for dep in row["depends_on"]
                )
                if all_done:
                    row["state"] = "dispatchable"
                    advanced.append(tid)
        return advanced

    def find_dependent_tasks(self, depended_on_task_id: str) -> list[dict[str, Any]]:
        return [
            row for row in self.tasks.values() if depended_on_task_id in row["depends_on"]
        ]

    def advisory_lock_key_for(self, text: str) -> int:
        return hash(text) & 0x7FFFFFFF

    def try_advisory_lock(self, lock_key: int):
        return object()  # any non-None sentinel means "acquired"

    def release_advisory_lock(self, conn: Any) -> None:
        self.released_locks.append(conn)


class FakeVaultClient:
    """Same public surface as tests/fakes.py's FakeVaultClient -- a
    private copy here (rather than importing it) keeps this file able to
    run standalone and makes the retry-loop-specific bookkeeping
    (asset/agent_run history) easy to assert on directly."""

    def __init__(self) -> None:
        self._briefs: dict[str, dict[str, Any]] = {}
        self._agent_runs: dict[str, dict[str, Any]] = {}
        self._assets: dict[str, dict[str, Any]] = {}
        self._campaigns: dict[str, dict[str, Any]] = {}

    def __enter__(self) -> "FakeVaultClient":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        pass

    def get_or_create_campaign(self, run_name: str, *, function_id: str) -> str:
        for cid, row in self._campaigns.items():
            if row["name"] == run_name:
                return cid
        cid = str(uuid.uuid4())
        self._campaigns[cid] = {"id": cid, "name": run_name, "function_id": function_id}
        return cid

    def create_brief(self, *, title, body_text, campaign_id, function_id, opportunity_card_id=None):
        bid = str(uuid.uuid4())
        row = {"id": bid, "title": title, "body": body_text}
        self._briefs[bid] = row
        return row

    def get_brief(self, brief_id: str) -> dict:
        return self._briefs[brief_id]

    def create_agent_run(
        self,
        *,
        agent_name,
        campaign_id,
        function_id,
        status="succeeded",
        input_payload=None,
        output_payload=None,
    ) -> dict:
        aid = str(uuid.uuid4())
        row = {
            "id": aid,
            "agent_name": agent_name,
            "status": status,
            "input": input_payload or {},
            "output": output_payload or {},
        }
        self._agent_runs[aid] = row
        return row

    def update_agent_run(
        self, agent_run_id, *, status=None, output_payload=None, completed_at=None
    ) -> dict:
        row = self._agent_runs[agent_run_id]
        if status is not None:
            row["status"] = status
        if output_payload is not None:
            row["output"] = output_payload
        return row

    def create_asset(
        self,
        *,
        asset_type,
        agent_run_id,
        campaign_id,
        function_id,
        content_bytes,
        brief_id=None,
        predecessor_asset_id=None,
        approval_state="draft",
    ) -> dict:
        import base64
        import hashlib

        aid = str(uuid.uuid4())
        row = {
            "id": aid,
            "asset_type": asset_type,
            "agent_run_id": agent_run_id,
            "content_base64": base64.b64encode(content_bytes).decode("ascii"),
            "content_hash": hashlib.sha256(content_bytes).hexdigest(),
            "approval_state": approval_state,
        }
        self._assets[aid] = row
        return row

    def get_asset(self, asset_id: str) -> dict:
        return self._assets[asset_id]

    def get_cost(self, cost_id: str) -> dict:
        return {"id": cost_id, "amount": 0.0}


def _has_violation(draft_text: str) -> list[str]:
    """Shared verdict logic for both review kinds in these tests: a
    LinkedIn-shaped draft needs a URL carrying all 3 UTM params."""
    has_url = "http://" in draft_text or "https://" in draft_text
    has_all_utm = (
        "utm_source" in draft_text and "utm_medium" in draft_text and "utm_campaign" in draft_text
    )
    if has_url and not has_all_utm:
        return ["url-utm"]
    return []


class _RetryLoopGatewayClient:
    """Simulates BOTH the drafting-regeneration call (Insight-to-Story
    Editor) and the two QA-check calls (Brand Steward / Fact-Check
    Verdict) realistically enough to exercise a genuine multi-attempt
    converge-or-exhaust loop:

    - A regeneration call (system_prompt mentions "Insight-to-Story") is
      recognised by its revision_feedback field; `fix_on_attempt` controls
      which attempt number (1-indexed) finally produces a fully-fixed
      draft (utm_source/utm_medium/utm_campaign all present). Pass None
      to never fix it (exhaustion scenario). Every attempt before that
      returns a draft that still carries the violation, proving the loop
      actually iterates rather than succeeding by accident on attempt 1.
    - A QA-check call (system_prompt mentions "Brand Steward" or
      "Fact-Check Verdict") grades whatever draft_text it's handed with
      the same _has_violation rule for both review kinds -- so a fix that
      clears the shared rule clears both checks in the same attempt,
      matching how a real regenerated draft would be re-graded twice.
    """

    def __init__(self, *, fix_on_attempt: int | None):
        self.fix_on_attempt = fix_on_attempt
        self.regen_calls = 0
        self.qa_calls: list[str] = []

    def __enter__(self) -> "_RetryLoopGatewayClient":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        pass

    def complete(
        self,
        *,
        model: str,
        system_prompt: str,
        user_content: str,
        agent_run_id: str,
        **_kw: Any,
    ) -> dict[str, Any]:
        if "Insight-to-Story" in system_prompt:
            payload = json.loads(user_content)
            revision = payload.get("revision_feedback")
            if revision is None:
                # The very first (non-retry) draft -- never exercised by
                # these tests directly (they seed an already-drafted,
                # already-failing asset), kept for completeness.
                body = "Read more: http://example.com/no-utm-yet"
            else:
                self.regen_calls += 1
                fixed = self.fix_on_attempt is not None and self.regen_calls >= self.fix_on_attempt
                body = (
                    "Consolidation at scale, in practice. Read more: "
                    "https://www.canvasintelligence.com/insights?utm_source=linkedin"
                    "&utm_medium=social&utm_campaign=retry"
                    if fixed
                    else "Consolidation at scale, in practice. Read more: "
                    "http://example.com/still-no-utm-params"
                )
            content = json.dumps({"post": body, "cta_url": "", "pillar": "Consolidation at scale"})
        elif "Brand Steward" in system_prompt or "Fact-Check Verdict" in system_prompt:
            self.qa_calls.append(system_prompt[:20])
            payload = json.loads(user_content)
            violations = _has_violation(payload.get("draft_text", ""))
            content = json.dumps({"pass": not violations, "violations": violations})
        else:
            content = json.dumps({})

        return {
            "id": f"fake-{uuid.uuid4()}",
            "model": model,
            "content": content,
            "usage": {"input_tokens": 10, "output_tokens": 10},
            "agent_run_id": agent_run_id,
        }


class _NoOpPermissionCheck:
    """Stands in for the dynamically-loaded functions/02-.../permission_check
    module: no client references, VIOLATION_CODE unused unless a test
    swaps this out for _AlwaysUnclearedPermissionCheck."""

    VIOLATION_CODE = "uncleared-client-reference"

    @staticmethod
    def find_uncleared_references(_candidate_names: list[str]) -> list[Any]:
        return []


class _AlwaysUnclearedPermissionCheck:
    """Simulates a NEVER_RETRYABLE violation: some client reference is
    always uncleared, regardless of draft content -- proves the retry
    loop is never entered for this violation class, on the first attempt
    or any later one."""

    VIOLATION_CODE = "uncleared-client-reference"

    @staticmethod
    def find_uncleared_references(_candidate_names: list[str]) -> list[Any]:
        return [object()]


def _envelope(task_id: str, task_type: str) -> TaskEnvelope:
    return TaskEnvelope(
        task_id=uuid.UUID(task_id),
        task_type=task_type,
        agent_run_id=uuid.uuid4(),
        campaign_id=uuid.uuid4(),
        created_at=datetime.now(timezone.utc),
    )


def _seed_full_lineage(
    db: FakeTaskDB, vault: FakeVaultClient, *, bad_draft_text: str
) -> tuple[str, str, str, str]:
    """plan -> brief -> draft-insight-to-story -> [qa-review-brand-steward,
    qa-review-fact-check], matching weekly-content-loop.yaml's real round-34
    per-draft graph shape. Returns (brief_id, draft_id, qa_bs_id, qa_fc_id)."""
    plan_id = str(uuid.uuid4())
    brief = vault.create_brief(
        title="weekly brief", body_text="Consolidation at scale proof point.",
        campaign_id="c", function_id="41",
    )
    db.seed(plan_id, "plan-content-monday")
    brief_task_id = str(uuid.uuid4())
    db.seed(
        brief_task_id, "draft-research-brief", depends_on=[plan_id],
        result_ref={
            "brief_id": brief["id"], "pillar": "Consolidation at scale",
            "vertical": "logistics", "audience_note": "multi-entity CFOs",
        },
    )
    draft_asset = vault.create_asset(
        asset_type="linkedin_post", agent_run_id=str(uuid.uuid4()), campaign_id="c",
        function_id=dispatch.FUNCTION_ID_39, content_bytes=bad_draft_text.encode("utf-8"),
    )
    draft_id = str(uuid.uuid4())
    db.seed(
        draft_id, "draft-insight-to-story", depends_on=[brief_task_id],
        result_ref={
            "vault_asset_id": draft_asset["id"], "content_hash": draft_asset["content_hash"],
            "pillar": "Consolidation at scale",
        },
    )
    qa_bs_id = str(uuid.uuid4())
    qa_fc_id = str(uuid.uuid4())
    db.seed(qa_bs_id, "qa-review-brand-steward", depends_on=[draft_id])
    db.seed(qa_fc_id, "qa-review-fact-check", depends_on=[draft_id])
    return brief_task_id, draft_id, qa_bs_id, qa_fc_id


BAD_DRAFT = "Consolidation at scale, in practice. Read more: http://example.com/no-utm-params"


@pytest.fixture()
def permission_check(monkeypatch):
    monkeypatch.setattr(dispatch, "load_permission_check", lambda: _NoOpPermissionCheck)
    return _NoOpPermissionCheck


def test_retry_loop_succeeds_after_multiple_attempts(monkeypatch, permission_check):
    db = FakeTaskDB()
    vault = FakeVaultClient()
    _brief_id, draft_id, qa_bs_id, qa_fc_id = _seed_full_lineage(
        db, vault, bad_draft_text=BAD_DRAFT
    )

    gateway = _RetryLoopGatewayClient(fix_on_attempt=2)
    monkeypatch.setattr(dispatch, "build_vault_client", lambda: vault)
    monkeypatch.setattr(dispatch, "build_gateway_client", lambda: gateway)

    dispatch.qa_review_brand_steward_handler(
        qa_bs_id, _envelope(qa_bs_id, "qa-review-brand-steward"), db
    )

    # 2 regeneration attempts were needed (attempt 1 still failed) --
    # proves the loop actually iterates, not just succeeds by luck.
    assert gateway.regen_calls == 2

    assert db.get_task(qa_bs_id)["state"] == TaskStateEnum.COMPLETED.value
    assert db.get_task(qa_fc_id)["state"] == TaskStateEnum.COMPLETED.value
    bs_ref = db.get_result_ref(qa_bs_id)
    fc_ref = db.get_result_ref(qa_fc_id)
    assert bs_ref["pass"] is True and fc_ref["pass"] is True
    assert bs_ref["retry_attempts"] == 2
    # Both sibling tasks point at the SAME final corrected asset, so
    # whichever one Friday's lineage walk resolves through carries the
    # fixed content, not the original violating draft.
    assert bs_ref["vault_asset_id"] == fc_ref["vault_asset_id"]
    # The draft task's own result_ref was overwritten with the corrected
    # asset too, for any future lineage walk that lands on it directly.
    assert db.get_result_ref(draft_id)["vault_asset_id"] == bs_ref["vault_asset_id"]


def test_retry_loop_exhausts_and_escalates_to_teams(monkeypatch, permission_check):
    db = FakeTaskDB()
    vault = FakeVaultClient()
    _brief_id, draft_id, qa_bs_id, qa_fc_id = _seed_full_lineage(
        db, vault, bad_draft_text=BAD_DRAFT
    )

    gateway = _RetryLoopGatewayClient(fix_on_attempt=None)  # never fixes it
    monkeypatch.setattr(dispatch, "build_vault_client", lambda: vault)
    monkeypatch.setattr(dispatch, "build_gateway_client", lambda: gateway)

    escalations: list[dict[str, Any]] = []
    monkeypatch.setattr(
        teams_notify,
        "notify_retry_exhausted",
        lambda **kwargs: escalations.append(kwargs) or True,
    )

    dispatch.qa_review_fact_check_handler(
        qa_fc_id, _envelope(qa_fc_id, "qa-review-fact-check"), db
    )

    assert gateway.regen_calls == dispatch.MAX_QA_RETRY_ATTEMPTS
    assert db.get_task(qa_fc_id)["state"] == TaskStateEnum.FAILED.value
    assert db.get_task(qa_bs_id)["state"] == TaskStateEnum.FAILED.value
    assert db.get_task(qa_fc_id)["_last_reason"] == TransitionReason.QA_BLOCKED.value

    assert len(escalations) == 1
    card = escalations[0]
    assert card["attempts"] == dispatch.MAX_QA_RETRY_ATTEMPTS
    assert "url-utm" in card["violations"]
    # A real track-changes diff, not an empty string -- original and final
    # attempt genuinely differ (the URL text itself changed each round).
    assert card["diff_text"].strip() != ""


def test_never_retryable_violation_skips_the_retry_loop_entirely(monkeypatch):
    """uncleared-client-reference must hard-stop on the FIRST attempt,
    exactly like the pre-retry-loop behaviour -- a regeneration attempt
    could itself fabricate a client name, so this is never retried."""
    db = FakeTaskDB()
    vault = FakeVaultClient()
    _brief_id, draft_id, qa_bs_id, qa_fc_id = _seed_full_lineage(
        db, vault, bad_draft_text="Clean text, no url at all."
    )
    monkeypatch.setattr(dispatch, "load_permission_check", lambda: _AlwaysUnclearedPermissionCheck)

    gateway = _RetryLoopGatewayClient(fix_on_attempt=1)
    monkeypatch.setattr(dispatch, "build_vault_client", lambda: vault)
    monkeypatch.setattr(dispatch, "build_gateway_client", lambda: gateway)

    needs_edit_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        teams_notify, "notify_needs_edit", lambda **kwargs: needs_edit_calls.append(kwargs) or True
    )

    dispatch.qa_review_brand_steward_handler(
        qa_bs_id, _envelope(qa_bs_id, "qa-review-brand-steward"), db
    )

    assert gateway.regen_calls == 0  # never entered the retry loop at all
    assert db.get_task(qa_bs_id)["state"] == TaskStateEnum.FAILED.value
    ref = db.get_result_ref(qa_bs_id)
    assert "uncleared-client-reference" in ref["violations"]
    assert len(needs_edit_calls) == 1


def test_sibling_lock_contention_falls_back_to_single_shot(monkeypatch, permission_check):
    """If a sibling review task already owns the retry loop for this
    draft (advisory lock unavailable), this task must NOT hang or error
    -- it falls straight through to the pre-retry-loop single-shot
    failure outcome, immediately."""
    db = FakeTaskDB()
    vault = FakeVaultClient()
    _brief_id, draft_id, qa_bs_id, qa_fc_id = _seed_full_lineage(
        db, vault, bad_draft_text=BAD_DRAFT
    )
    monkeypatch.setattr(db, "try_advisory_lock", lambda lock_key: None)  # sibling owns it

    gateway = _RetryLoopGatewayClient(fix_on_attempt=1)
    monkeypatch.setattr(dispatch, "build_vault_client", lambda: vault)
    monkeypatch.setattr(dispatch, "build_gateway_client", lambda: gateway)

    needs_edit_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        teams_notify, "notify_needs_edit", lambda **kwargs: needs_edit_calls.append(kwargs) or True
    )

    dispatch.qa_review_brand_steward_handler(
        qa_bs_id, _envelope(qa_bs_id, "qa-review-brand-steward"), db
    )

    assert gateway.regen_calls == 0  # never became the owner, never regenerated anything
    assert db.get_task(qa_bs_id)["state"] == TaskStateEnum.FAILED.value
    assert len(needs_edit_calls) == 1
