"""Process 5, "review against brand and factual rules": the gate that
decides what may be published now enforces its own contracts.

Four things were wrong with it, all verified against the code:

  * **The verdict was fail-open.** Both review paths did
    `violations = list(verdict.get("violations") or [])` and then
    `passed = not violations`, never reading `pass` at all -- and neither
    function 02 nor function 48 had its output validated anywhere, the
    only stage in the pipeline with no contract enforcement on either
    side. So `{}` scored zero violations and published, and an explicit
    `{"pass": false, "violations": [], "notes": "..."}` was overridden
    into a pass.

  * **The deterministic clearance check was dead code.** The weekly path
    called `find_uncleared_references([])` -- a literal empty list, which
    cannot return anything however the register is configured. On all six
    Wednesday drafts the only non-model protection against naming an
    uncleared client did nothing.

  * **Every weekly draft was reviewed as `channel: "linkedin"`**, a
    literal, including the newsletter (email) and the case study (web).

  * **The carousel's Canva bulk-create CSV was reviewed as copy**, having
    already been stripped for the Teams excerpt but not for the reviewer.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from orchestrator import dispatch
from tests.fakes import patch_dispatch_clients
from tests.test_dispatch import FakeTaskDB, _envelope


class _VerdictGatewayClient:
    """Returns a caller-supplied verdict for the QA functions, so a test
    can pose a specific malformed or self-contradictory response."""

    def __init__(self, verdict: dict[str, Any]) -> None:
        from tests.fakes import FakeGatewayClient

        self._inner = FakeGatewayClient()
        self._verdict = verdict
        self.calls: list[dict[str, Any]] = []

    def __enter__(self) -> "_VerdictGatewayClient":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        pass

    def complete(self, **kw: Any) -> dict[str, Any]:
        self.calls.append(kw)
        prompt = kw["system_prompt"]
        if "Brand Steward" in prompt or "Fact-Check Verdict" in prompt:
            return {
                "id": "fake",
                "model": kw["model"],
                "content": json.dumps(self._verdict),
                "usage": {"input_tokens": 1, "output_tokens": 1},
                "agent_run_id": kw["agent_run_id"],
            }
        return self._inner.complete(**kw)


@pytest.fixture()
def clients(monkeypatch):
    return patch_dispatch_clients(monkeypatch)


def _seed_reviewable_draft(
    db: FakeTaskDB,
    vault: Any,
    *,
    draft_task_type: str = "draft-newsletter",
    draft_text: str = "One governed source of truth. Read more.",
    proof_points: list[dict[str, str]] | None = None,
) -> tuple[str, str]:
    """A completed drafting task with a real asset, plus its QA task."""
    draft_id, qa_id = str(uuid.uuid4()), str(uuid.uuid4())
    db.seed(draft_id, draft_task_type)
    db.seed(qa_id, "qa-review-brand-steward", depends_on=[draft_id])
    asset = vault.create_asset(
        asset_type="newsletter",
        agent_run_id=None,
        campaign_id=None,
        function_id=dispatch.FUNCTION_ID_46,
        content_bytes=draft_text.encode("utf-8"),
        approval_state="draft",
    )
    db.set_result_ref(
        draft_id,
        {
            "vault_asset_id": asset["id"],
            "content_hash": asset["content_hash"],
            "pillar": "Consolidation at scale",
            "campaign": "consolidation-at-scale",
            "proof_points": proof_points or [],
        },
    )
    db.transition(draft_id, dispatch.TaskStateEnum.COMPLETED, dispatch.TransitionReason.COMPLETED)
    return draft_id, qa_id


def _run_review(monkeypatch, db, verdict, qa_id, task_type="qa-review-brand-steward"):
    gateway = _VerdictGatewayClient(verdict)
    monkeypatch.setattr(dispatch, "build_gateway_client", lambda: gateway)
    handler = dispatch.DISPATCH_TABLE[task_type]
    handler(qa_id, _envelope(qa_id, task_type), db)
    return gateway


def test_empty_verdict_object_cannot_pass_a_draft(clients, monkeypatch):
    """`{}` used to score zero violations and publish. All three fields
    are required by the output schema, so validating it makes the
    malformed response a dead letter instead of a silent approval."""
    db = FakeTaskDB()
    _draft_id, qa_id = _seed_reviewable_draft(db, clients)

    with pytest.raises(dispatch.DispatchError, match="required property"):
        _run_review(monkeypatch, db, {}, qa_id)

    assert db.get_task(qa_id)["state"] != "completed"


def test_declared_failure_without_a_code_still_blocks(clients, monkeypatch):
    """A refusal whose reason lives in `notes` rather than in a violation
    code was overridden into a pass, because nothing read `pass`."""
    # draft-content-repurpose has no regeneration recipe
    # (_DRAFT_REGEN_PARAMS), so this goes straight to the single-shot
    # failure outcome and the assertion is about the verdict rather than
    # about the retry loop, which has its own tests.
    db = FakeTaskDB()
    _draft_id, qa_id = _seed_reviewable_draft(
        db, clients, draft_task_type="draft-content-repurpose"
    )

    _run_review(
        monkeypatch,
        db,
        {"pass": False, "violations": [], "notes": "the second claim is not supported"},
        qa_id,
    )

    assert db.get_task(qa_id)["state"] == "failed"
    ref = db.get_result_ref(qa_id)
    assert ref["violations"] == [dispatch.QA_VERDICT_UNSPECIFIED_FAILURE]
    assert ref["pass"] is False


def test_a_clean_verdict_still_passes(clients, monkeypatch):
    """The guards above must not turn every review into a block."""
    db = FakeTaskDB()
    _draft_id, qa_id = _seed_reviewable_draft(db, clients)

    _run_review(monkeypatch, db, {"pass": True, "violations": [], "notes": ""}, qa_id)

    assert db.get_task(qa_id)["state"] == "completed"
    assert db.get_result_ref(qa_id)["pass"] is True


def test_an_uncleared_name_in_the_draft_blocks_even_if_the_model_missed_it(
    clients, monkeypatch
):
    """The deterministic backstop, which used to be handed a literal empty
    list. The model here returns a clean verdict; the register does not."""
    db = FakeTaskDB()
    _draft_id, qa_id = _seed_reviewable_draft(
        db,
        clients,
        draft_text="We consolidated Imperial's 14 ERP systems into one governed lakehouse.",
    )

    _run_review(monkeypatch, db, {"pass": True, "violations": [], "notes": ""}, qa_id)

    assert db.get_task(qa_id)["state"] == "failed"
    assert "uncleared-client-reference" in db.get_result_ref(qa_id)["violations"]


def test_clean_copy_raises_no_clearance_violation(clients, monkeypatch):
    """The backstop is register-bound, not a general name detector: copy
    naming no registered client must pass untouched."""
    db = FakeTaskDB()
    _draft_id, qa_id = _seed_reviewable_draft(
        db,
        clients,
        draft_text="A listed group consolidated 40+ business units into one governed lakehouse.",
    )

    _run_review(monkeypatch, db, {"pass": True, "violations": [], "notes": ""}, qa_id)

    assert db.get_task(qa_id)["state"] == "completed"


@pytest.mark.parametrize(
    "draft_task_type,expected_channel",
    [
        ("draft-newsletter", "email"),
        ("draft-case-study", "web"),
        ("draft-carousel-post", "linkedin"),
        ("draft-insight-to-story", "linkedin"),
    ],
)
def test_each_draft_is_reviewed_as_its_own_channel(
    clients, monkeypatch, draft_task_type, expected_channel
):
    """All six were reviewed as "linkedin", a literal. Function 02's
    checks 4 and 5 only branch on internal-brief today, so no verdict
    changed -- but the agent_run records this as the evidence of what was
    reviewed, and it was wrong for two of the six."""
    db = FakeTaskDB()
    _draft_id, qa_id = _seed_reviewable_draft(db, clients, draft_task_type=draft_task_type)

    gateway = _run_review(
        monkeypatch, db, {"pass": True, "violations": [], "notes": ""}, qa_id
    )

    reviewed = [
        json.loads(call["user_content"])
        for call in gateway.calls
        if "Brand Steward" in call["system_prompt"]
    ]
    assert reviewed[0]["channel"] == expected_channel
    dispatch._validate_function_input(dispatch.FUNCTION_ID_02, reviewed[0])


def test_the_carousel_csv_manifest_is_not_reviewed_as_copy(clients, monkeypatch):
    """_render_carousel appends a Canva bulk-create CSV to the asset. It
    was already stripped for the Teams excerpt and not for the reviewer,
    so function 02 was reading machine columns as marketing copy. Nothing
    is lost: every cell is generated from the slide text above it."""
    db = FakeTaskDB()
    slides = "[CAROUSEL]\nSlide 1: One number - everyone agrees\n"
    csv = f"{dispatch.CAROUSEL_BULK_CSV_MARKER}\nslide_number,headline,subhead,image_ref\n1,x,y,,"
    _draft_id, qa_id = _seed_reviewable_draft(
        db, clients, draft_task_type="draft-carousel-post", draft_text=slides + csv
    )

    gateway = _run_review(
        monkeypatch, db, {"pass": True, "violations": [], "notes": ""}, qa_id
    )

    reviewed = json.loads(gateway.calls[0]["user_content"])["draft_text"]
    assert "One number - everyone agrees" in reviewed
    assert dispatch.CAROUSEL_BULK_CSV_MARKER not in reviewed
    assert "brand_template_id" not in reviewed


def test_the_fact_checker_receives_the_weeks_cited_evidence(clients, monkeypatch):
    """F-FACT-CHECK-BLIND. Function 48's three standing lists are a
    snapshot of positioning.md, so a claim from this week's scan was
    fabricated by definition -- the better processes 1-4 worked, the more
    Thursday blocked. Pieter's sign-off, 1 Sep 2026."""
    db = FakeTaskDB()
    evidence = [
        {
            "claim": "Reporting cycles fell from nine days to two",
            "source": "https://www.moneyweb.co.za/feed/",
        }
    ]
    draft_id, _qa_id = _seed_reviewable_draft(db, clients, proof_points=evidence)
    fc_id = str(uuid.uuid4())
    db.seed(fc_id, "qa-review-fact-check", depends_on=[draft_id])

    gateway = _run_review(
        monkeypatch,
        db,
        {"pass": True, "violations": [], "notes": ""},
        fc_id,
        task_type="qa-review-fact-check",
    )

    sent = json.loads(gateway.calls[0]["user_content"])
    assert sent["proof_points"] == evidence
    dispatch._validate_function_input(dispatch.FUNCTION_ID_48_FACT_CHECK, sent)


def test_the_brand_steward_is_not_given_proof_points(clients, monkeypatch):
    """Function 02's input schema is additionalProperties: false and has
    no proof_points -- List D is the fact-checker's business, and sending
    it to the wrong function would be rejected by its own contract."""
    db = FakeTaskDB()
    _draft_id, qa_id = _seed_reviewable_draft(
        db, clients, proof_points=[{"claim": "x y z", "source": "https://example.com"}]
    )

    gateway = _run_review(
        monkeypatch, db, {"pass": True, "violations": [], "notes": ""}, qa_id
    )

    sent = json.loads(gateway.calls[0]["user_content"])
    assert "proof_points" not in sent
    dispatch._validate_function_input(dispatch.FUNCTION_ID_02, sent)


def test_a_week_with_no_evidence_sends_an_empty_list_not_a_missing_field(
    clients, monkeypatch
):
    """An evidence-free week is a real outcome, and List D being empty
    must not look like the field never arrived."""
    db = FakeTaskDB()
    draft_id, _qa_id = _seed_reviewable_draft(db, clients, proof_points=[])
    fc_id = str(uuid.uuid4())
    db.seed(fc_id, "qa-review-fact-check", depends_on=[draft_id])

    gateway = _run_review(
        monkeypatch,
        db,
        {"pass": True, "violations": [], "notes": ""},
        fc_id,
        task_type="qa-review-fact-check",
    )

    sent = json.loads(gateway.calls[0]["user_content"])
    assert sent["proof_points"] == []
    dispatch._validate_function_input(dispatch.FUNCTION_ID_48_FACT_CHECK, sent)
