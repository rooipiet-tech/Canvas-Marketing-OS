"""Process 4, "draft content": the six Wednesday drafting handlers now send
their functions what those functions actually declare they take.

Five of the six shared one handler that sent every one of them the same
four fields -- `{brief, pillar, vertical, audience_note}` -- while all five
schemas are `additionalProperties: false`, reject three of those four, and
require a `campaign` slug that was never sent at all. Two consequences:

  * The proof points never arrived. Function 39's prompt.md is written
    against "the supplied `proof_point`"; nothing supplied one. #119 put
    function 41's structured {claim, source} proof points onto the
    result_ref; this is the stage that reads them.

  * Every utm_campaign was invented by the model. Each prompt requires
    utm parameters on the single call to action and no campaign value was
    ever passed, so one week's brief produced six assets carrying six
    unrelated attribution tags -- and process 8 ("measure") attributes by
    campaign.

Two of the six cannot be called honestly at all yet, and complete as
deliberately-not-attempted rather than drafting: function 43 requires an
`executive_name` that exists nowhere in this repository, and function 47
requires a real engagement while docs/permission-register.yaml clears no
client. Pieter's direction, 1 Sep 2026: no executive and no client
engagement to be named yet.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from orchestrator import dispatch
from tests.fakes import patch_dispatch_clients
from tests.test_dispatch import FakeTaskDB, _envelope

DRAFT_TASKS = [
    ("draft-insight-to-story", dispatch.FUNCTION_ID_39),
    ("draft-executive-ghostwrite", dispatch.FUNCTION_ID_43),
    ("draft-carousel-post", dispatch.FUNCTION_ID_45),
    ("draft-newsletter", dispatch.FUNCTION_ID_46),
    ("draft-case-study", dispatch.FUNCTION_ID_47),
]

ATTEMPTED = ["draft-insight-to-story", "draft-carousel-post", "draft-newsletter"]
UNATTEMPTED = {
    "draft-executive-ghostwrite": "no_executive_configured",
    "draft-case-study": "no_cleared_engagement",
}


class _RecordingGatewayClient:
    """Records every complete() call so a test can assert on the exact
    payload each function was sent, not merely that a call happened."""

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


@pytest.fixture()
def clients(monkeypatch):
    return patch_dispatch_clients(monkeypatch)


@pytest.fixture()
def gateway(monkeypatch, clients):
    recorder = _RecordingGatewayClient()
    monkeypatch.setattr(dispatch, "build_gateway_client", lambda: recorder)
    return recorder


def _run_week(
    db: FakeTaskDB, *, proof_points: list[dict[str, str]] | None = None
) -> dict[str, str]:
    """Drives Monday's plan, Tuesday's brief and its QA gate, then every
    Wednesday drafting task, and returns the task ids by task_type."""
    ids = {
        name: str(uuid.uuid4())
        for name in ["plan", "brief", "qa-brief", *[t for t, _ in DRAFT_TASKS]]
    }
    db.seed(ids["plan"], "plan-content-monday")
    db.seed(ids["brief"], "draft-research-brief", depends_on=[ids["plan"]])
    db.seed(ids["qa-brief"], "qa-review", depends_on=[ids["brief"]])
    for task_type, _fn in DRAFT_TASKS:
        db.seed(ids[task_type], task_type, depends_on=[ids["qa-brief"]])

    dispatch.plan_content_monday_handler(
        ids["plan"], _envelope(ids["plan"], "plan-content-monday"), db
    )
    dispatch.draft_research_brief_handler(
        ids["brief"], _envelope(ids["brief"], "draft-research-brief"), db
    )
    if proof_points is not None:
        # Overrides what the fake model returned, so a test can pose the
        # evidence-free week without needing a second fake branch.
        ref = dict(db.get_result_ref(ids["brief"]))
        ref["proof_points"] = proof_points
        ref["proof_point_count"] = len(proof_points)
        db.set_result_ref(ids["brief"], ref)

    dispatch.qa_review_handler(ids["qa-brief"], _envelope(ids["qa-brief"], "qa-review"), db)

    for task_type, _fn in DRAFT_TASKS:
        dispatch.DISPATCH_TABLE[task_type](
            ids[task_type], _envelope(ids[task_type], task_type), db
        )
    return ids


def test_brief_fields_survive_the_qa_passthrough(clients):
    """F-BRIEF-FIELDS-DROPPED-BY-QA. Inserting tuesday-qa-research-brief
    between the brief and the six drafts (process 3's review gate) moved
    where resolve_lineage_result stops: it began stopping at the QA task,
    which forwarded brief_id but none of the brief's own fields, so every
    draft's pillar silently became None. No loop test walks a brief
    through a review into a draft, so nothing caught it."""
    db = FakeTaskDB()
    ids = _run_week(db)
    qa_ref = db.get_result_ref(ids["qa-brief"])

    assert qa_ref["pillar"], "the QA gate dropped the brief's pillar"
    assert qa_ref["proof_points"], "the QA gate dropped the brief's proof points"
    for key in dispatch.BRIEF_CARRIED_KEYS:
        assert key in qa_ref


def test_qa_passthrough_invents_no_brief_fields_for_the_daily_loop(clients):
    """The same qa-review handler serves the daily loop, whose lineage has
    no brief-shaped fields at all. Carrying them forward must not mean
    minting nulls for a lineage that never had them."""
    db = FakeTaskDB()
    ingest_id, draft_id, qa_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    db.seed(ingest_id, "ingest-signals")
    db.seed(draft_id, "draft-brief", depends_on=[ingest_id])
    db.seed(qa_id, "qa-review", depends_on=[draft_id])

    dispatch.ingest_signals_handler(ingest_id, _envelope(ingest_id, "ingest-signals"), db)
    dispatch.draft_brief_handler(draft_id, _envelope(draft_id, "draft-brief"), db)
    dispatch.qa_review_handler(qa_id, _envelope(qa_id, "qa-review"), db)

    ref = db.get_result_ref(qa_id)
    assert ref["pass"] is True
    assert "proof_points" not in ref
    assert "pillar_source" not in ref


@pytest.mark.parametrize("task_type", ATTEMPTED)
def test_each_draft_payload_satisfies_its_own_input_schema(clients, gateway, task_type):
    """The contract, enforced end to end: whatever the handler actually
    put on the wire validates against that function's schema.json. Before
    this change every one of these payloads was rejected by its own
    schema on all four counts."""
    db = FakeTaskDB()
    _run_week(db)

    function_id = dict(DRAFT_TASKS)[task_type]
    prompt_title = {
        "draft-insight-to-story": "Insight-to-Story Editor",
        "draft-carousel-post": "Carousel/Document Post Writer",
        "draft-newsletter": "Email/Newsletter Writer",
    }[task_type]
    sent = [
        json.loads(call["user_content"])
        for call in gateway.calls
        if prompt_title in call["system_prompt"]
    ]
    assert len(sent) == 1, f"expected exactly one {task_type} call, got {len(sent)}"
    # Raises DispatchError if the payload is off-contract.
    dispatch._validate_function_input(function_id, sent[0])


def test_the_whole_week_shares_one_campaign_tag(clients, gateway):
    """F-CAMPAIGN-TAG-INVENTED. One brief, one pillar, one attribution
    tag -- otherwise process 8 has six unrelated utm_campaign values for a
    single week's content and nothing to attribute performance to."""
    db = FakeTaskDB()
    ids = _run_week(db)

    pillar = db.get_result_ref(ids["brief"])["pillar"]
    expected = dispatch._campaign_slug(pillar)
    tags = {
        json.loads(call["user_content"])["campaign"]
        for call in gateway.calls
        if "campaign" in json.loads(call["user_content"])
    }
    assert tags == {expected}
    # And the same tag is recorded on each draft's result_ref, so a later
    # measurement step can find it without re-deriving it.
    for task_type in ATTEMPTED:
        assert db.get_result_ref(ids[task_type])["campaign"] == expected


def test_campaign_slug_matches_every_schema_pattern():
    """Each of the six schemas pins `campaign` to the same pattern. Every
    pillar the planner can choose must satisfy it -- a pillar that slugged
    badly would fail validation only on the week it was chosen."""
    import re

    pattern = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
    for pillar in dispatch.CONTENT_PILLARS:
        assert pattern.match(dispatch._campaign_slug(pillar)), pillar


@pytest.mark.parametrize("task_type,expected_status", sorted(UNATTEMPTED.items()))
def test_functions_needing_facts_we_do_not_have_decline(
    clients, gateway, task_type, expected_status
):
    """Function 43 needs an executive's name; function 47 needs a real
    engagement. Neither exists, so both complete having written nothing
    rather than putting a placeholder into a required field -- which for a
    ghostwritten voice or a case study means publishing a fabrication."""
    db = FakeTaskDB()
    ids = _run_week(db)

    assert db.get_task(ids[task_type])["state"] == "completed"
    ref = db.get_result_ref(ids[task_type])
    assert ref["status"] == expected_status
    assert "vault_asset_id" not in ref
    assert ref["reason"]
    # Nothing was sent to a model at all.
    prompt_title = {
        "draft-executive-ghostwrite": "Executive Ghostwriter",
        "draft-case-study": "Case Study Writer",
    }[task_type]
    assert not [c for c in gateway.calls if prompt_title in c["system_prompt"]]


def test_evidence_free_week_declines_carousel_and_newsletter(clients, gateway):
    """Functions 45 and 46 both require proof_points with minItems 1 and
    neither has function 39's gap-statement clause: a carousel is one
    proof point per slide, a newsletter is the week's proof points. With
    none, the honest outcome is no asset -- not an invented one."""
    db = FakeTaskDB()
    ids = _run_week(db, proof_points=[])

    for task_type in ["draft-carousel-post", "draft-newsletter"]:
        assert db.get_task(ids[task_type])["state"] == "completed"
        assert db.get_result_ref(ids[task_type])["status"] == "no_evidence"


def test_evidence_free_week_asks_function_39_to_flag_the_gap(clients, gateway):
    """Function 39 is the exception, and by its own schema: `proof_point`
    is documented as "When no evidence has been documented yet, state that
    plainly here -- the editor must flag the gap rather than fabricate a
    proof point", with the matching rule in its prompt.md. So the gap
    statement is the contract's instruction, not a placeholder."""
    db = FakeTaskDB()
    ids = _run_week(db, proof_points=[])

    assert db.get_task(ids["draft-insight-to-story"])["state"] == "completed"
    sent = [
        json.loads(c["user_content"])
        for c in gateway.calls
        if "Insight-to-Story Editor" in c["system_prompt"]
    ]
    assert sent[0]["proof_point"] == dispatch.NO_EVIDENCE_PROOF_POINT
    dispatch._validate_function_input(dispatch.FUNCTION_ID_39, sent[0])


def test_proof_points_reach_the_drafts_with_their_sources(clients, gateway):
    """"Proof over platitude": a drafting function that cannot see where a
    claim came from cannot honour its own never-fabricate rule, so the
    {claim, source} pair is flattened, not truncated to the claim."""
    db = FakeTaskDB()
    ids = _run_week(db)

    brief_points = db.get_result_ref(ids["brief"])["proof_points"]
    assert brief_points, "fixture regression: the fake brief carries no proof points"
    sent = [
        json.loads(c["user_content"])
        for c in gateway.calls
        if "Email/Newsletter Writer" in c["system_prompt"]
    ][0]
    for point in brief_points:
        assert any(point["claim"] in line for line in sent["proof_points"])
        assert any(point["source"] in line for line in sent["proof_points"])


def test_qa_gate_reviews_an_undrafted_task_as_skipped_not_blocked(clients):
    """A deliberately-undrafted task reaching Thursday must not report a
    QA violation every week: crying wolf trains a reader to ignore a real
    QA_BLOCKED. It completes, marked with the draft's own status, and
    still carries no vault_asset_id for Friday to publish."""
    db = FakeTaskDB()
    ids = _run_week(db)
    qa_id = str(uuid.uuid4())
    db.seed(qa_id, "qa-review-brand-steward", depends_on=[ids["draft-case-study"]])

    dispatch.qa_review_brand_steward_handler(
        qa_id, _envelope(qa_id, "qa-review-brand-steward"), db
    )

    assert db.get_task(qa_id)["state"] == "completed"
    ref = db.get_result_ref(qa_id)
    assert ref["status"] == "no_cleared_engagement"
    assert ref["reviewed"] is False
    assert not ref.get("pass")
    assert "vault_asset_id" not in ref


def test_qa_gate_still_blocks_when_an_asset_genuinely_went_missing(clients):
    """The skip above must not weaken the existing guard: a draft that
    completed with no asset AND no status explaining itself is an asset
    that went missing, and QA_BLOCKED still errs toward blocking."""
    db = FakeTaskDB()
    draft_id, qa_id = str(uuid.uuid4()), str(uuid.uuid4())
    db.seed(draft_id, "draft-newsletter")
    db.seed(qa_id, "qa-review-brand-steward", depends_on=[draft_id])
    db.set_result_ref(draft_id, {"campaign_id": str(uuid.uuid4()), "pillar": "Fabric-native"})
    db.transition(draft_id, dispatch.TaskStateEnum.COMPLETED, dispatch.TransitionReason.COMPLETED)

    dispatch.qa_review_brand_steward_handler(
        qa_id, _envelope(qa_id, "qa-review-brand-steward"), db
    )

    assert db.get_task(qa_id)["state"] == "failed"
    assert db.get_result_ref(qa_id)["violations"] == ["no_reviewable_asset"]


def test_friday_completes_cleanly_for_a_draft_that_was_never_written(clients):
    """The whole undrafted path, Wednesday to Friday. Without this,
    Friday dead-letters on the missing content_hash every week for a gap
    that is already recorded on the drafting task's own result_ref --
    a permanent red mark standing for a deliberate decision."""
    db = FakeTaskDB()
    ids = _run_week(db)
    qa_brand, qa_fact, friday = (str(uuid.uuid4()) for _ in range(3))
    ghostwrite = ids["draft-executive-ghostwrite"]
    db.seed(qa_brand, "qa-review-brand-steward", depends_on=[ghostwrite])
    db.seed(qa_fact, "qa-review-fact-check", depends_on=[ghostwrite])
    db.seed(friday, "schedule-social-buffer", depends_on=[qa_brand, qa_fact])

    dispatch.qa_review_brand_steward_handler(
        qa_brand, _envelope(qa_brand, "qa-review-brand-steward"), db
    )
    dispatch.qa_review_fact_check_handler(qa_fact, _envelope(qa_fact, "qa-review-fact-check"), db)
    dispatch.schedule_social_buffer_handler(
        friday, _envelope(friday, "schedule-social-buffer"), db
    )

    assert db.get_task(friday)["state"] == "completed"
    ref = db.get_result_ref(friday)
    assert ref["published"] is False
    assert ref["status"] == "no_executive_configured"


def test_friday_still_dead_letters_when_a_reviewed_draft_lost_its_hash(clients):
    """The clean completion above is keyed on the explicit status marker
    and nothing else -- a QA gate that reviewed a real draft but carries
    no content_hash is still the failure it has always been."""
    db = FakeTaskDB()
    qa_id, friday = str(uuid.uuid4()), str(uuid.uuid4())
    db.seed(qa_id, "qa-review-brand-steward")
    db.seed(friday, "schedule-social-buffer", depends_on=[qa_id])
    db.set_result_ref(qa_id, {"pass": True, "agent_run_id": str(uuid.uuid4())})
    db.transition(qa_id, dispatch.TaskStateEnum.COMPLETED, dispatch.TransitionReason.COMPLETED)

    with pytest.raises(dispatch.DispatchError, match="no content_hash"):
        dispatch.schedule_social_buffer_handler(
            friday, _envelope(friday, "schedule-social-buffer"), db
        )
