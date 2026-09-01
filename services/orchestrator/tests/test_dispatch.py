"""Direct, no-live-infra unit tests of orchestrator/dispatch.py's 5
handlers (plan steps 6-13; AC-01, AC-02, AC-05, AC-06, AC-30, AC-31(c)).

Uses tests/fakes.py's in-memory client fakes (gateway/vault/gatekeeper/
mcp-web) AND an in-memory FakeTaskDB standing in for orchestrator.db (no
live Postgres needed) so every handler's real LOGIC — lineage resolution,
result_ref shape, terminal-state selection, proof-circuit tagging — is
exercised directly, independent of both live external services and a live
database. Complements (not replaces) test_worker_loop.py's live-Postgres
end-to-end proof and tests/e2e's live-cmos-dev proof.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from orchestrator import dispatch
from orchestrator.models import TaskEnvelope
from tests.fakes import (
    FakeGatekeeperClient,
)


class FakeTaskDB:
    """In-memory stand-in for orchestrator.db's task_state surface."""

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
        if task_id not in self.tasks:
            raise RuntimeError(f"no such task {task_id} (mirrors a real FK violation)")
        self.tasks[task_id]["state"] = to_state_val

    def advance_dependents(self, completed_task_id: str) -> list[str]:
        advanced = []
        for tid, row in self.tasks.items():
            if completed_task_id in row["depends_on"] and row["state"] == "pending":
                all_done = all(
                    self.tasks.get(dep, {}).get("state") == "completed" for dep in row["depends_on"]
                )
                if all_done:
                    row["state"] = "dispatchable"
                    advanced.append(tid)
        return advanced

    def set_result_ref(self, task_id: str, result_ref: dict[str, Any]) -> None:
        self.tasks[task_id]["result_ref"] = result_ref

    def get_result_ref(self, task_id: str) -> dict[str, Any] | None:
        return self.tasks[task_id]["result_ref"]

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        return self.tasks.get(task_id)

    def get_tasks(self, task_ids: list[str]) -> list[dict[str, Any]]:
        return [self.tasks[t] for t in task_ids if t in self.tasks]


def _envelope(task_id: str, task_type: str, *, proof_circuit: bool = False) -> TaskEnvelope:
    from datetime import datetime, timezone

    return TaskEnvelope(
        task_id=uuid.UUID(task_id),
        task_type=task_type,
        agent_run_id=uuid.uuid4(),
        campaign_id=uuid.uuid4(),
        created_at=datetime.now(timezone.utc),
        metadata={"proof_circuit": "true"} if proof_circuit else None,
    )


@pytest.fixture()
def clients(monkeypatch):
    from tests.fakes import patch_dispatch_clients

    vault = patch_dispatch_clients(monkeypatch)
    return vault


def test_ingest_signals_completes_and_stores_result_ref(clients):
    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    db.seed(task_id, "ingest-signals")

    envelope = _envelope(task_id, "ingest-signals")
    dispatch.ingest_signals_handler(task_id, envelope, db)

    assert db.get_task(task_id)["state"] == "completed"
    ref = db.get_result_ref(task_id)
    assert ref["vault_signal_id"]
    assert ref["campaign_id"]


def test_draft_brief_reads_ingest_result_via_lineage(clients):
    db = FakeTaskDB()
    ingest_id = str(uuid.uuid4())
    score_id = str(uuid.uuid4())  # legacy pass-through task -- no result_ref ever set
    draft_id = str(uuid.uuid4())
    db.seed(ingest_id, "ingest-signals")
    db.seed(score_id, "score-signals", depends_on=[ingest_id])
    db.seed(draft_id, "draft-brief", depends_on=[score_id])

    ingest_envelope = _envelope(ingest_id, "ingest-signals")
    dispatch.ingest_signals_handler(ingest_id, ingest_envelope, db)

    draft_envelope = _envelope(draft_id, "draft-brief")
    dispatch.draft_brief_handler(draft_id, draft_envelope, db)

    assert db.get_task(draft_id)["state"] == "completed"
    ref = db.get_result_ref(draft_id)
    assert ref["brief_id"]
    assert ref["executive_brief_id"]


def test_qa_review_passes_clean_draft_from_brief(clients):
    db = FakeTaskDB()
    ingest_id, draft_id, qa_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    db.seed(ingest_id, "ingest-signals")
    db.seed(draft_id, "draft-brief", depends_on=[ingest_id])
    db.seed(qa_id, "qa-review", depends_on=[draft_id])

    dispatch.ingest_signals_handler(ingest_id, _envelope(ingest_id, "ingest-signals"), db)
    dispatch.draft_brief_handler(draft_id, _envelope(draft_id, "draft-brief"), db)
    dispatch.qa_review_handler(qa_id, _envelope(qa_id, "qa-review"), db)

    assert db.get_task(qa_id)["state"] == "completed"
    assert db.get_result_ref(qa_id)["pass"] is True


class _RecordingGatewayClient:
    """Wraps FakeGatewayClient and records every kwarg each `complete()`
    call was made with, so a test can assert exactly which calls set
    `content_class` and to what value -- identical pattern to
    test_dispatch_ingest_redaction.py's own helper of the same name,
    duplicated locally to keep this file self-contained."""

    def __init__(self) -> None:
        from tests.fakes import FakeGatewayClient

        self._inner = FakeGatewayClient()
        self.calls: list[dict[str, Any]] = []

    def __enter__(self) -> "_RecordingGatewayClient":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        pass

    def complete(self, **kw: Any) -> dict[str, Any]:
        self.calls.append(kw)
        return self._inner.complete(**kw)


# F-QA-REVIEW-PUBLIC-SOURCE (4 Aug 2026, heartbeat round 17, Pieter's
# explicit ruling: "same answer, it's already public, ingest it or go
# with it" -- extending F-INGEST-PUBLIC-SOURCE's exemption one hop
# downstream, to qa-review's own review of a draft-brief that was
# rendered directly from that same already-public content). Proves the
# scope precisely: draft-brief lineage (channel=="internal-brief", renamed
# from "web" in round 18 -- see F-BRIEF-CTA-UTM-EXEMPT below) DOES carry
# the exemption.
#
# F-QA-REVIEW-DRAFT-CONTENT-PUBLIC-SOURCE (5 Aug 2026, heartbeat round 19,
# Pieter's ruling: "Same answer as before" -- extending the exemption a
# second time, now to draft-content lineage (channel=="linkedin") too.
# Round 18's PR #68 had deliberately left this lineage un-exempted on the
# assumption it would only ever contain "client-free generic" content and
# therefore never trip full-name-like -- that assumption held only until
# round 19's F-PROMPT-OUTPUT-CONTRACT fix let draft-content produce real
# output for the first time, which immediately tripped the pattern on
# ordinary Canvas brand/product phrasing. See dispatch.py's own comment
# at this call site for the full account.


def test_qa_review_of_brief_sets_public_source_content_class(clients, monkeypatch):
    db = FakeTaskDB()
    ingest_id, draft_id, qa_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    db.seed(ingest_id, "ingest-signals")
    db.seed(draft_id, "draft-brief", depends_on=[ingest_id])
    db.seed(qa_id, "qa-review", depends_on=[draft_id])

    dispatch.ingest_signals_handler(ingest_id, _envelope(ingest_id, "ingest-signals"), db)
    dispatch.draft_brief_handler(draft_id, _envelope(draft_id, "draft-brief"), db)

    recorder = _RecordingGatewayClient()
    monkeypatch.setattr(dispatch, "build_gateway_client", lambda: recorder)
    dispatch.qa_review_handler(qa_id, _envelope(qa_id, "qa-review"), db)

    assert db.get_task(qa_id)["state"] == "completed"
    assert recorder.calls, "expected a gateway.complete() call"
    assert recorder.calls[0].get("content_class") == "public_source_content"


def test_qa_review_of_draft_content_sets_public_source_content_class(clients, monkeypatch):
    db = FakeTaskDB()
    content_id, qa_id = str(uuid.uuid4()), str(uuid.uuid4())
    db.seed(content_id, "draft-content")
    db.seed(qa_id, "qa-review", depends_on=[content_id])

    dispatch.draft_content_handler(content_id, _envelope(content_id, "draft-content"), db)

    recorder = _RecordingGatewayClient()
    monkeypatch.setattr(dispatch, "build_gateway_client", lambda: recorder)
    dispatch.qa_review_handler(qa_id, _envelope(qa_id, "qa-review"), db)

    assert db.get_task(qa_id)["state"] == "completed"
    assert recorder.calls, "expected a gateway.complete() call"
    assert recorder.calls[0].get("content_class") == "public_source_content"


def test_qa_review_blocks_missing_utm_and_never_completes(clients):
    """AC-05: a seeded missing-UTM draft is caught, and the task's
    terminal state is NOT completed (so a dependent request-approval task
    would never advance).

    Moved from the draft-brief lineage to the draft-content (channel==
    "linkedin") lineage on 4 Aug 2026 (heartbeat round 18,
    F-BRIEF-CTA-UTM-EXEMPT) -- see test_qa_review_of_internal_brief_
    passes_despite_missing_cta_and_utm below for why: the draft-brief
    lineage is now exempt from url-utm/missing-cta by Pieter's ruling, so
    AC-05's original scenario no longer proves url-utm enforcement there.
    draft-content is genuine customer-facing content and remains fully
    subject to this check, so AC-05's proof lives here now instead."""
    db = FakeTaskDB()
    content_id, qa_id, approval_id = (
        str(uuid.uuid4()),
        str(uuid.uuid4()),
        str(uuid.uuid4()),
    )
    db.seed(content_id, "draft-content")
    db.seed(qa_id, "qa-review", depends_on=[content_id])
    db.seed(approval_id, "request-approval", depends_on=[qa_id])

    dispatch.draft_content_handler(content_id, _envelope(content_id, "draft-content"), db)

    # Corrupt the post text post-hoc so it deliberately omits UTM params,
    # simulating AC-05's seeded violation without needing draft-content
    # itself to produce bad content.
    import base64

    asset_id = db.get_result_ref(content_id)["vault_asset_id"]
    bad_text = "A plain link with no utm params at all: https://www.canvasintelligence.com/x"
    clients._assets[asset_id]["content_base64"] = base64.b64encode(bad_text.encode("utf-8")).decode(
        "ascii"
    )

    dispatch.qa_review_handler(qa_id, _envelope(qa_id, "qa-review"), db)

    assert db.get_task(qa_id)["state"] == "failed"
    ref = db.get_result_ref(qa_id)
    assert ref["pass"] is False
    assert "url-utm" in ref["violations"]
    # request-approval never advances past pending -- it stays absent from
    # RUNNING/COMPLETED entirely (AC-05's verify text).
    assert db.get_task(approval_id)["state"] == "dispatchable"  # never even advanced


def test_qa_review_of_internal_brief_passes_despite_missing_cta_and_utm(clients):
    """F-BRIEF-CTA-UTM-EXEMPT (4 Aug 2026, heartbeat round 18). Pieter's
    ruling on the round-18 open question: "Go with a for daily briefs" --
    option (a), exempt internal daily briefs from function 02's universal
    missing-cta/url-utm rules entirely. The draft-brief lineage now sets
    channel="internal-brief" (renamed from "web"), and prompt.md's checks
    4/5 explicitly exempt that channel. Proves the exemption end-to-end:
    a brief with no CTA marker and a bare, non-UTM citation link still
    reaches qa-review's real terminal state of "completed", and its
    dependent request-approval task correctly advances -- the opposite
    outcome from the pre-fix behaviour this same scenario used to produce
    (see the AC-05 test above, now moved to draft-content)."""
    db = FakeTaskDB()
    ingest_id, draft_id, qa_id, approval_id = (
        str(uuid.uuid4()),
        str(uuid.uuid4()),
        str(uuid.uuid4()),
        str(uuid.uuid4()),
    )
    db.seed(ingest_id, "ingest-signals")
    db.seed(draft_id, "draft-brief", depends_on=[ingest_id])
    db.seed(qa_id, "qa-review", depends_on=[draft_id])
    db.seed(approval_id, "request-approval", depends_on=[qa_id])

    dispatch.ingest_signals_handler(ingest_id, _envelope(ingest_id, "ingest-signals"), db)
    dispatch.draft_brief_handler(draft_id, _envelope(draft_id, "draft-brief"), db)

    # Corrupt the brief body post-hoc so it carries neither a CTA marker
    # nor a UTM-tagged link -- the exact AC-05 seeded violation, replayed
    # here against the now-exempt internal-brief channel.
    brief_id = db.get_result_ref(draft_id)["brief_id"]
    clients._briefs[brief_id]["body"] = (
        "Microsoft shipped new Fabric capacity tooling this window. "
        "Source: https://learn.microsoft.com/en-us/fabric/get-started/whats-new"
    )

    dispatch.qa_review_handler(qa_id, _envelope(qa_id, "qa-review"), db)

    assert db.get_task(qa_id)["state"] == "completed"
    ref = db.get_result_ref(qa_id)
    assert ref["pass"] is True
    # (dispatch.py's success result_ref carries no "violations" key at all
    # -- only the FAILED path stores one; "completed" + pass=True together
    # already prove zero violations were raised.)
    # This FakeTaskDB doesn't model real pending-until-dependencies-complete
    # semantics (seed() always starts a task at "dispatchable", and
    # advance_dependents() only moves a "pending" row) -- so unlike AC-05's
    # negative proof above (qa-review returning early on failure, before
    # ever calling advance_dependents), there's no observable state change
    # to assert on approval_id here. The important proof is qa-review's own
    # terminal state and result_ref above: "completed"/pass=True is the
    # precondition advance_dependents needs to ever let request-approval
    # run at all. approval_id stays at its seeded default either way.
    assert db.get_task(approval_id)["state"] == "dispatchable"


def test_qa_review_blocks_uncleared_client_reference(clients, monkeypatch):
    """AC-06: the uncleared-client-block is proven via a dedicated
    invocation path (a deliberately supplied client_references entry),
    never via the live published asset."""
    db = FakeTaskDB()
    content_id = str(uuid.uuid4())
    qa_id = str(uuid.uuid4())
    db.seed(content_id, "draft-content")
    db.seed(qa_id, "qa-review", depends_on=[content_id])

    dispatch.draft_content_handler(content_id, _envelope(content_id, "draft-content"), db)

    # Force a client_references entry onto this qa-review invocation to
    # prove the block fires -- monkeypatch the handler's otherwise-empty
    # client_references list by patching qa_review_handler's local closure
    # is awkward, so instead assert the underlying mechanism directly:
    # permission_check.find_uncleared_references (the SAME function the
    # handler calls) blocks a fabricated name exactly as DE-4 requires.
    permission_check = dispatch.load_permission_check()
    uncleared = permission_check.find_uncleared_references(["Totally Fabricated Client Co"])
    assert uncleared
    assert uncleared[0].violation_code == "uncleared-client-reference"

    # And the live published content (draft-content's own asset) carries
    # zero client_references -- proven by the fact draft_content_handler
    # never threads a client_reference into function 42's input at all.
    dispatch.qa_review_handler(qa_id, _envelope(qa_id, "qa-review", proof_circuit=True), db)
    assert db.get_task(qa_id)["state"] == "completed"


def test_draft_content_is_client_free(clients):
    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    db.seed(task_id, "draft-content")

    envelope = _envelope(task_id, "draft-content", proof_circuit=True)
    dispatch.draft_content_handler(task_id, envelope, db)

    ref = db.get_result_ref(task_id)
    assert ref["vault_asset_id"]
    assert ref["content_hash"]
    asset = clients.get_asset(ref["vault_asset_id"])
    import base64

    text = base64.b64decode(asset["content_base64"]).decode("utf-8")
    for client_name in ("Imperial", "Rotork", "Weir", "ArcelorMittal", "SGB Cape", "Delta"):
        assert client_name not in text


def test_draft_content_tags_agent_run_with_loop_proof_name(clients):
    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    db.seed(task_id, "draft-content")

    envelope = _envelope(task_id, "draft-content", proof_circuit=True)
    dispatch.draft_content_handler(task_id, envelope, db)

    ref = db.get_result_ref(task_id)
    agent_run = clients.get_agent_run(ref["agent_run_id"])
    assert agent_run["agent_name"] == dispatch.AGENT_NAME_LOOP_PROOF == "loop-proof-circuit"


def test_request_approval_completes_synchronously_never_polling(clients):
    db = FakeTaskDB()
    content_id, qa_id, approval_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    db.seed(content_id, "draft-content")
    db.seed(qa_id, "qa-review", depends_on=[content_id])
    db.seed(approval_id, "request-approval", depends_on=[qa_id])

    content_envelope = _envelope(content_id, "draft-content", proof_circuit=True)
    dispatch.draft_content_handler(content_id, content_envelope, db)
    dispatch.qa_review_handler(qa_id, _envelope(qa_id, "qa-review", proof_circuit=True), db)
    dispatch.request_approval_handler(
        approval_id, _envelope(approval_id, "request-approval", proof_circuit=True), db
    )

    assert db.get_task(approval_id)["state"] == "completed"
    ref = db.get_result_ref(approval_id)
    assert ref["decision_id"]
    assert ref["outcome"] == "escalated"


def test_request_approval_uses_real_publish_function_id_and_proof_tags(clients, monkeypatch):
    db = FakeTaskDB()
    content_id, qa_id, approval_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    db.seed(content_id, "draft-content")
    db.seed(qa_id, "qa-review", depends_on=[content_id])
    db.seed(approval_id, "request-approval", depends_on=[qa_id])

    content_envelope = _envelope(content_id, "draft-content", proof_circuit=True)
    dispatch.draft_content_handler(content_id, content_envelope, db)
    dispatch.qa_review_handler(qa_id, _envelope(qa_id, "qa-review", proof_circuit=True), db)

    captured = {}

    from orchestrator import dispatch as dispatch_module

    class SpyGatekeeperClient:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            pass

        def gate_check(self, **kwargs):
            captured.update(kwargs)
            return FakeGatekeeperClient().gate_check(**kwargs)

    monkeypatch.setattr(dispatch_module, "build_gatekeeper_client", lambda: SpyGatekeeperClient())

    dispatch.request_approval_handler(
        approval_id, _envelope(approval_id, "request-approval", proof_circuit=True), db
    )

    assert captured["function_id"] == "publish.social_post"
    assert captured["action_class"] == "publish"
    assert captured["preview_reference"].startswith("loop-proof://")
    assert captured["preview_title"].startswith("[LOOP-PROOF] ")


def test_request_approval_uses_qa_ancestors_real_agent_run_id_not_envelopes(clients, monkeypatch):
    """ROUND 34 regression: contracts/vault-schema/schema.sql declares
    gate_decisions.agent_run_id NOT NULL FK -> agent_runs. envelope.
    agent_run_id (worker.py's synthetic uuid5(event_id, source_task_id))
    is never written to agent_runs by any handler, so passing it to a
    REAL /gate-check call always 500s with ForeignKeyViolation -- this
    was never caught before because FakeGatekeeperClient doesn't enforce
    the FK. Confirms request_approval_handler now passes the QA task's
    own agent_run_id (a real row qa_review_handler creates via
    vault.create_agent_run) instead of its own envelope.agent_run_id."""
    db = FakeTaskDB()
    content_id, qa_id, approval_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    db.seed(content_id, "draft-content")
    db.seed(qa_id, "qa-review", depends_on=[content_id])
    db.seed(approval_id, "request-approval", depends_on=[qa_id])

    content_envelope = _envelope(content_id, "draft-content", proof_circuit=True)
    dispatch.draft_content_handler(content_id, content_envelope, db)
    dispatch.qa_review_handler(qa_id, _envelope(qa_id, "qa-review", proof_circuit=True), db)
    qa_ref = db.get_result_ref(qa_id)

    captured = {}

    from orchestrator import dispatch as dispatch_module

    class SpyGatekeeperClient:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            pass

        def gate_check(self, **kwargs):
            captured.update(kwargs)
            return FakeGatekeeperClient().gate_check(**kwargs)

    monkeypatch.setattr(dispatch_module, "build_gatekeeper_client", lambda: SpyGatekeeperClient())

    approval_envelope = _envelope(approval_id, "request-approval", proof_circuit=True)
    dispatch.request_approval_handler(approval_id, approval_envelope, db)

    assert captured["agent_run_id"] == qa_ref["agent_run_id"]
    assert captured["agent_run_id"] != str(approval_envelope.agent_run_id)

    approval_ref = db.get_result_ref(approval_id)
    assert approval_ref["agent_run_id"] == qa_ref["agent_run_id"]


def test_legacy_pass_through_unchanged_for_real_s10_s11_types(clients):
    """AC-02: an already-real S10/S11 task_type still reaches completed +
    advance_dependents fires, exactly as before this session."""
    db = FakeTaskDB()
    scan_id = str(uuid.uuid4())
    dependent_id = str(uuid.uuid4())
    db.seed(scan_id, "competitor-discovery-scan")
    db.tasks[dependent_id] = {
        "task_id": dependent_id,
        "task_type": "dedupe-signal-cards",
        "state": "pending",
        "depends_on": [scan_id],
        "result_ref": None,
    }

    dispatch.dispatch_task(_envelope(scan_id, "competitor-discovery-scan"), db)

    assert db.get_task(scan_id)["state"] == "completed"
    assert db.get_task(dependent_id)["state"] == "dispatchable"


def test_unrecognized_task_type_raises_not_silently_completed():
    """AC-02: a synthetic never-registered task_type with NO backing row
    raises (mirrors a real FK violation) rather than being silently marked
    completed."""
    db = FakeTaskDB()
    fake_task_id = str(uuid.uuid4())  # deliberately never seeded

    with pytest.raises(RuntimeError):
        dispatch.dispatch_task(_envelope(fake_task_id, "zzz-unregistered-test-type"), db)


# F-WEEKLY-LOOP-DRAFT-PUBLIC-SOURCE (7 Aug 2026, heartbeat round 20,
# Pieter's explicit ruling via AskUserQuestion: "Extend the exemption").
#
# Before this fix, none of weekly-content-loop's 6 Wednesday drafting
# handlers (the callers of _draft_social_post_handler) had ANY dispatch-
# level test coverage at all -- their first real-world exercise was
# today's live heartbeat run, where 5 of the 6 (every one whose research
# brief happened to name an executive, client, or case-study subject)
# dead-lettered on REDACTION_BLOCKED/full-name-like after 3 retries each,
# cascading to kill qa-review-brand-steward and draft-content-repurpose
# too -- see cmos-burndown-tracker.md's round-20 entry for the full
# incident account. This proves the fix the same way test_dispatch_
# ingest_redaction.py's test_ingest_signals_sets_public_source_content_
# class and this file's own test_qa_review_of_brief_sets_public_source_
# content_class do: build the real plan-content-monday -> draft-research-
# brief -> draft-insight-to-story lineage chain (one representative
# caller of the shared handler is enough -- all 6 route through the same
# _complete_and_meter call), swap in a gateway double that records every
# kwarg, and assert content_class was actually forwarded on the wire.
def test_draft_social_post_sets_public_source_content_class(clients, monkeypatch):
    db = FakeTaskDB()
    plan_id, brief_id, draft_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    db.seed(plan_id, "plan-content-monday")
    db.seed(brief_id, "draft-research-brief", depends_on=[plan_id])
    db.seed(draft_id, "draft-insight-to-story", depends_on=[brief_id])

    dispatch.plan_content_monday_handler(plan_id, _envelope(plan_id, "plan-content-monday"), db)
    dispatch.draft_research_brief_handler(
        brief_id, _envelope(brief_id, "draft-research-brief"), db
    )

    recorder = _RecordingGatewayClient()
    monkeypatch.setattr(dispatch, "build_gateway_client", lambda: recorder)
    dispatch.draft_insight_to_story_handler(
        draft_id, _envelope(draft_id, "draft-insight-to-story"), db
    )

    assert db.get_task(draft_id)["state"] == "completed"
    assert recorder.calls, "expected a gateway.complete() call"
    assert recorder.calls[0].get("content_class") == "public_source_content"


# F-CONTENT-REPURPOSE-RACE (7 Aug 2026, heartbeat round 22 discovery, fixed
# round 23) -- see dispatch.py's own note above draft_content_repurpose_
# handler for the full account. Before this fix, draft-content-repurpose
# unconditionally raised DispatchError("...ancestor result_ref carries no
# brief_id") on every real run once its ancestors (draft-newsletter /
# draft-case-study) actually completed, because it routed through
# _draft_social_post_handler's resolve_lineage_result walk -- built for a
# brief_id-shaped result_ref, which neither ancestor's own result_ref ever
# carries (round 22's live trace called this a "race"; it wasn't -- see the
# dispatch.py comment for why it was 100% deterministic once reachable).
def test_draft_content_repurpose_reads_newsletter_source_asset(clients):
    db = FakeTaskDB()
    plan_id, brief_id, newsletter_id, repurpose_id = (
        str(uuid.uuid4()),
        str(uuid.uuid4()),
        str(uuid.uuid4()),
        str(uuid.uuid4()),
    )
    db.seed(plan_id, "plan-content-monday")
    db.seed(brief_id, "draft-research-brief", depends_on=[plan_id])
    db.seed(newsletter_id, "draft-newsletter", depends_on=[brief_id])
    db.seed(repurpose_id, "draft-content-repurpose", depends_on=[newsletter_id])

    dispatch.plan_content_monday_handler(plan_id, _envelope(plan_id, "plan-content-monday"), db)
    dispatch.draft_research_brief_handler(
        brief_id, _envelope(brief_id, "draft-research-brief"), db
    )
    dispatch.draft_newsletter_handler(
        newsletter_id, _envelope(newsletter_id, "draft-newsletter"), db
    )

    dispatch.draft_content_repurpose_handler(
        repurpose_id, _envelope(repurpose_id, "draft-content-repurpose"), db
    )

    assert db.get_task(repurpose_id)["state"] == "completed"
    ref = db.get_result_ref(repurpose_id)
    assert ref["vault_asset_id"]
    assert ref["pillar"]


def test_draft_content_repurpose_sets_public_source_content_class(clients, monkeypatch):
    db = FakeTaskDB()
    plan_id, brief_id, newsletter_id, repurpose_id = (
        str(uuid.uuid4()),
        str(uuid.uuid4()),
        str(uuid.uuid4()),
        str(uuid.uuid4()),
    )
    db.seed(plan_id, "plan-content-monday")
    db.seed(brief_id, "draft-research-brief", depends_on=[plan_id])
    db.seed(newsletter_id, "draft-newsletter", depends_on=[brief_id])
    db.seed(repurpose_id, "draft-content-repurpose", depends_on=[newsletter_id])

    dispatch.plan_content_monday_handler(plan_id, _envelope(plan_id, "plan-content-monday"), db)
    dispatch.draft_research_brief_handler(
        brief_id, _envelope(brief_id, "draft-research-brief"), db
    )
    dispatch.draft_newsletter_handler(
        newsletter_id, _envelope(newsletter_id, "draft-newsletter"), db
    )

    recorder = _RecordingGatewayClient()
    monkeypatch.setattr(dispatch, "build_gateway_client", lambda: recorder)
    dispatch.draft_content_repurpose_handler(
        repurpose_id, _envelope(repurpose_id, "draft-content-repurpose"), db
    )

    assert db.get_task(repurpose_id)["state"] == "completed"
    assert recorder.calls, "expected a gateway.complete() call"
    assert recorder.calls[0].get("content_class") == "public_source_content"

    # The source text sent to the model must be the newsletter's OWN
    # drafted text (schema.json's source_asset_summary/pillar/campaign/
    # target_formats input contract) -- proves the fix reads the right
    # ancestor's asset instead of trying (and failing) to resolve a
    # brief_id that never exists on this lineage.
    import json

    sent = json.loads(recorder.calls[0]["user_content"])
    assert sent["source_asset_summary"]
    assert "target_formats" in sent
    assert "campaign" in sent
    assert "brief_id" not in sent


def test_draft_content_repurpose_falls_back_to_case_study(clients):
    """_select_repurpose_source falls back to draft-case-study when
    draft-newsletter has no reviewable asset (e.g. left un-dispatched or
    dead-lettered) -- proves the fallback path, not just the primary one."""
    db = FakeTaskDB()
    plan_id, brief_id, newsletter_id, case_study_id, repurpose_id = (
        str(uuid.uuid4()),
        str(uuid.uuid4()),
        str(uuid.uuid4()),
        str(uuid.uuid4()),
        str(uuid.uuid4()),
    )
    db.seed(plan_id, "plan-content-monday")
    db.seed(brief_id, "draft-research-brief", depends_on=[plan_id])
    db.seed(newsletter_id, "draft-newsletter", depends_on=[brief_id])
    db.seed(case_study_id, "draft-case-study", depends_on=[brief_id])
    db.seed(repurpose_id, "draft-content-repurpose", depends_on=[newsletter_id, case_study_id])

    dispatch.plan_content_monday_handler(plan_id, _envelope(plan_id, "plan-content-monday"), db)
    dispatch.draft_research_brief_handler(
        brief_id, _envelope(brief_id, "draft-research-brief"), db
    )
    # The case study's result_ref is constructed rather than produced by
    # draft_case_study_handler: while docs/permission-register.yaml clears
    # no client, function 47 declines to draft (F-NO-CLEARED-ENGAGEMENT)
    # and can no longer mint a source asset. The fallback it stands for is
    # not dead code -- _select_repurpose_source knows nothing of that
    # policy, and the branch carries traffic again the day an engagement
    # is cleared -- so the state is set up directly, in the exact shape
    # _draft_social_post_handler writes.
    db.set_result_ref(
        case_study_id,
        {
            "vault_asset_id": clients.create_asset(
                asset_type="case_study",
                agent_run_id=None,
                campaign_id=None,
                function_id=dispatch.FUNCTION_ID_47,
                content_bytes=b"A cleared client engagement, drafted.",
                approval_state="draft",
            )["id"],
            "content_hash": "seeded",
            "pillar": "Consolidation at scale",
            "campaign": "consolidation-at-scale",
        },
    )
    db.transition(
        case_study_id, dispatch.TaskStateEnum.COMPLETED, dispatch.TransitionReason.COMPLETED
    )
    # newsletter is left seeded but never actually run -- its result_ref
    # stays None, standing in for "not a reviewable source" without needing
    # a real dead-letter transition in this fake DB.

    dispatch.draft_content_repurpose_handler(
        repurpose_id, _envelope(repurpose_id, "draft-content-repurpose"), db
    )

    assert db.get_task(repurpose_id)["state"] == "completed"
    # The new asset's own vault_asset_id is necessarily a fresh id (the
    # repurposed derivative, not the source) -- what proves the fallback
    # fired is which ancestor's task_type was actually read from, recorded
    # in this run's own agent_run input_payload.
    last_agent_run = list(clients._agent_runs.values())[-1]
    assert last_agent_run["input"]["source_task_type"] == "draft-case-study"


def test_draft_content_repurpose_raises_when_no_source_available(clients):
    """Both ancestors present but neither ever completed with an asset
    (both left seeded/un-dispatched) -- must raise DispatchError, never
    silently complete with an empty/fabricated result_ref."""
    db = FakeTaskDB()
    newsletter_id, case_study_id, repurpose_id = (
        str(uuid.uuid4()),
        str(uuid.uuid4()),
        str(uuid.uuid4()),
    )
    db.seed(newsletter_id, "draft-newsletter")
    db.seed(case_study_id, "draft-case-study")
    db.seed(repurpose_id, "draft-content-repurpose", depends_on=[newsletter_id, case_study_id])

    with pytest.raises(dispatch.DispatchError):
        dispatch.draft_content_repurpose_handler(
            repurpose_id, _envelope(repurpose_id, "draft-content-repurpose"), db
        )


def test_fact_check_prompt_covers_every_pillar_lead_proof():
    """Round 24 regression guard for the bug found 7 Aug 2026: function 48's
    List A cited 'docs/positioning.md §7' (a section with no proof points
    at all -- it is titled 'Gaps and watch-outs') and only ever covered
    the Finance-grade trust and Fabric-native pillars' proof, not the
    other three. Since function 41 instructs every draft to lead with its
    assigned pillar's own proof point, this meant any draft assigned to
    Consolidation at scale, Productised speed or Beyond the dashboard was
    guaranteed to fail fact-check on its own correctly-sourced lead claim
    -- confirmed live when all 6 Wednesday drafts failed in the same run.
    This test does not re-verify the model's behaviour (that needs a real
    completion); it guards the prompt file itself against silently
    regressing back to the incomplete/mislabelled list."""
    raw_prompt = dispatch._read_prompt("48-fact-check-verdict")
    # Collapse whitespace/newlines so a phrase that's markdown-wrapped
    # across lines in the prompt file still matches a single-line needle.
    prompt = " ".join(raw_prompt.split())

    # The correction note at the end legitimately quotes the old, wrong
    # citation as history -- only the active "### List A" heading itself
    # must never regress back to citing §7 (a section with no proof
    # points at all).
    list_a_heading = next(line for line in raw_prompt.splitlines() if line.startswith("### List A"))
    assert "§7" not in list_a_heading, (
        "List A's source citation regressed to the wrong section (§7 has no "
        "proof points -- see the round 24 correction note in prompt.md)"
    )

    # One traceable phrase per pillar not already covered by the original
    # company-wide list (Finance-grade trust and Fabric-native were never
    # the gap -- see the round 24 correction note).
    pillar_phrases = [
        "40+ business units, 14+ ERP systems",  # Consolidation at scale
        "8 entities, 3 countries, 4 currencies",  # Consolidation at scale
        "turnkey DaaS platform",  # Productised speed
        "exception management",  # Beyond the dashboard
    ]
    for phrase in pillar_phrases:
        assert phrase in prompt, f"fact-check prompt.md is missing pillar proof point: {phrase!r}"

    # The confidentiality note must survive alongside the new pillar
    # proof points -- these are anonymised shapes, not license to name
    # the underlying client.
    assert "uncleared-client-reference" in prompt


# ---------------------------------------------------------------------------
# F-JSON-TRAILING-CONTENT (round 30) — _parse_json_content tolerating a chatty model
# ---------------------------------------------------------------------------


def test_parse_json_content_accepts_a_fenced_object_with_closing_fence():
    """The exact 10 Aug 05:00 UTC failure: a ```json fence whose CLOSING
    fence sat on its own line after the object. The old strip("`") only
    removed backticks at the two ends of the whole string, so the closing
    fence survived and json.loads died with 'Extra data: line 6 column 1'."""
    content = (
        '```json\n{\n  "pass": false,\n  "violations": ["unsupported-claim"],\n'
        '  "notes": "x"\n}\n```'
    )
    assert dispatch._parse_json_content(content) == {
        "pass": False,
        "violations": ["unsupported-claim"],
        "notes": "x",
    }


def test_parse_json_content_accepts_trailing_prose_after_the_object():
    content = '{"pass": true, "violations": []}\n\nI checked each claim against the brief.'
    assert dispatch._parse_json_content(content) == {"pass": True, "violations": []}


def test_parse_json_content_accepts_a_preamble_before_the_object():
    content = 'Here is the verdict:\n{"pass": true, "violations": []}'
    assert dispatch._parse_json_content(content) == {"pass": True, "violations": []}


def test_parse_json_content_still_accepts_a_plain_bare_object():
    assert dispatch._parse_json_content('{"pass": true, "violations": []}') == {
        "pass": True,
        "violations": [],
    }


def test_parse_json_content_still_raises_on_genuinely_broken_json():
    """Round 28's truncation shape must still fail loudly -- this fix must
    not paper over a real max_tokens truncation."""
    with pytest.raises(dispatch.DispatchError, match="was not valid JSON"):
        dispatch._parse_json_content('{"pass": false, "violations": ["a"')


def test_parse_json_content_rejects_a_non_object_json_value():
    with pytest.raises(dispatch.DispatchError, match="not an object"):
        dispatch._parse_json_content("[1, 2, 3]")


def test_parse_json_content_logs_discarded_trailing_content(caplog):
    import logging as _logging

    with caplog.at_level(_logging.WARNING):
        dispatch._parse_json_content('{"pass": true}\ntrailing explanation here')
    assert "model_response_trailing_content_discarded" in caplog.text
