"""Process 6, "human approval": the approval card carries what a reviewer
needs to disagree with it.

Every content approval ever raised produced the same card. The handler
passed content_hash and nothing else -- preview_title stayed None outside
the proof-circuit path and evidence_summary was never sent at all -- so
the Gatekeeper's fallbacks filled in:

    Approval required: publish.social_post (publish)
    Preview:           publish.social_post (publish)
    Evidence:          Autonomy policy requires human approval for
                       publish.social_post / publish. Bound content hash:
                       3f2a...

Identical for this week's newsletter, last week's carousel and every
story between them. The "evidence" explains why the POLICY requires an
approval; it says nothing about what is being approved. A human handed
that can click approve or reject, but cannot dissent -- there is nothing
there to dissent from.

Everything needed was already on the lineage and simply never read.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from orchestrator import dispatch
from tests.fakes import patch_dispatch_clients
from tests.test_dispatch import FakeTaskDB, _envelope

EVIDENCE = [
    {
        "claim": "Reporting cycles fell from nine days to two",
        "source": "https://www.moneyweb.co.za/feed/",
    }
]


class _RecordingGatekeeper:
    """Captures the exact /gate-check body each approval was raised with."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __enter__(self) -> "_RecordingGatekeeper":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        pass

    def gate_check(self, **kw: Any) -> dict[str, Any]:
        self.calls.append(kw)
        return {
            "decision_id": str(uuid.uuid4()),
            "outcome": "escalated",
            "approval_id": str(uuid.uuid4()),
            "approve_url": "https://approval.invalid/a",
            "reject_url": "https://approval.invalid/r",
        }


@pytest.fixture()
def clients(monkeypatch):
    return patch_dispatch_clients(monkeypatch)


@pytest.fixture()
def gatekeeper(monkeypatch, clients):
    recorder = _RecordingGatekeeper()
    monkeypatch.setattr(dispatch, "build_gatekeeper_client", lambda: recorder)
    return recorder


def _seed_approved_draft(
    db: FakeTaskDB,
    vault: Any,
    *,
    draft_task_type: str = "draft-newsletter",
    draft_text: str = "One number everyone agrees on. Read more.",
    proof_points: list[dict[str, str]] | None = None,
    pillar_source: str = "signals",
    both_reviews: bool = True,
) -> str:
    """A draft through its Thursday gate(s), returning the Friday task id."""
    draft_id = str(uuid.uuid4())
    db.seed(draft_id, draft_task_type)
    asset = vault.create_asset(
        asset_type="newsletter",
        agent_run_id=None,
        campaign_id=None,
        function_id=dispatch.FUNCTION_ID_46,
        content_bytes=draft_text.encode("utf-8"),
        approval_state="draft",
    )
    gate_ids = []
    kinds = ["brand_steward", "fact_check"] if both_reviews else ["brand_steward"]
    for kind in kinds:
        gate_id = str(uuid.uuid4())
        db.seed(gate_id, f"qa-review-{kind}", depends_on=[draft_id])
        db.set_result_ref(
            gate_id,
            {
                "pass": True,
                "vault_asset_id": asset["id"],
                "content_hash": asset["content_hash"],
                "draft_task_id": draft_id,
                "draft_task_type": draft_task_type,
                "review_kind": kind,
                "agent_run_id": str(uuid.uuid4()),
                "pillar": "Fabric-native",
                "campaign": "fabric-native",
                "pillar_source": pillar_source,
                "proof_points": proof_points if proof_points is not None else EVIDENCE,
            },
        )
        db.transition(
            gate_id, dispatch.TaskStateEnum.COMPLETED, dispatch.TransitionReason.COMPLETED
        )
        gate_ids.append(gate_id)

    friday_id = str(uuid.uuid4())
    db.seed(friday_id, "schedule-social-buffer", depends_on=gate_ids)
    return friday_id


def _card(gatekeeper: _RecordingGatekeeper) -> dict[str, Any]:
    assert len(gatekeeper.calls) == 1
    return gatekeeper.calls[0]


def test_the_card_names_what_is_being_approved(clients, gatekeeper):
    """Two pending cards must be distinguishable. The title carries the
    asset kind, the pillar it was written to and the week's campaign
    tag -- the three things that differ between them."""
    db = FakeTaskDB()
    friday_id = _seed_approved_draft(db, clients)

    dispatch.schedule_social_buffer_handler(
        friday_id, _envelope(friday_id, "schedule-social-buffer"), db
    )

    title = _card(gatekeeper)["preview_title"]
    assert "Owned-channel newsletter" in title
    assert "Fabric-native" in title
    assert "fabric-native" in title
    assert title != "publish.social_post (publish)"


def test_the_card_carries_the_copy_and_its_citations(clients, gatekeeper):
    db = FakeTaskDB()
    friday_id = _seed_approved_draft(db, clients)

    dispatch.schedule_social_buffer_handler(
        friday_id, _envelope(friday_id, "schedule-social-buffer"), db
    )

    evidence = _card(gatekeeper)["evidence_summary"]
    assert "One number everyone agrees on" in evidence
    assert "Reporting cycles fell from nine days to two" in evidence
    assert "https://www.moneyweb.co.za/feed/" in evidence
    assert "utm_campaign=fabric-native" in evidence


def test_the_card_says_which_reviews_passed_it(clients, gatekeeper):
    """A Friday task depends on BOTH Thursday gates but lineage stops at
    one, so naming reviews from the single ancestor_ref would name one
    and imply the other."""
    db = FakeTaskDB()
    friday_id = _seed_approved_draft(db, clients)

    dispatch.schedule_social_buffer_handler(
        friday_id, _envelope(friday_id, "schedule-social-buffer"), db
    )

    evidence = _card(gatekeeper)["evidence_summary"]
    assert "brand_steward" in evidence
    assert "fact_check" in evidence


def test_the_card_does_not_claim_a_review_that_did_not_run(clients, gatekeeper):
    db = FakeTaskDB()
    friday_id = _seed_approved_draft(db, clients, both_reviews=False)

    dispatch.schedule_social_buffer_handler(
        friday_id, _envelope(friday_id, "schedule-social-buffer"), db
    )

    evidence = _card(gatekeeper)["evidence_summary"]
    assert "brand_steward" in evidence
    assert "fact_check" not in evidence


def test_an_absent_evidence_base_is_stated_not_omitted(clients, gatekeeper):
    """"No proof points" is a reason to reject. A card that silently drops
    the line is worse than one that says so out loud."""
    db = FakeTaskDB()
    friday_id = _seed_approved_draft(db, clients, proof_points=[])

    dispatch.schedule_social_buffer_handler(
        friday_id, _envelope(friday_id, "schedule-social-buffer"), db
    )

    evidence = _card(gatekeeper)["evidence_summary"]
    assert "Proof points: none supplied" in evidence


def test_a_calendar_chosen_pillar_says_so(clients, gatekeeper):
    """"The market chose this subject" and "the calendar chose it, there
    was no evidence this week" are very different claims about a week's
    content, and the approver is the person who should know which."""
    db = FakeTaskDB()
    friday_id = _seed_approved_draft(db, clients, pillar_source="rotation")

    dispatch.schedule_social_buffer_handler(
        friday_id, _envelope(friday_id, "schedule-social-buffer"), db
    )

    assert "calendar rotation" in _card(gatekeeper)["evidence_summary"]


def test_the_excerpt_is_bounded(clients, gatekeeper):
    """A newsletter body is thousands of characters; a card is not the
    place to paste one."""
    db = FakeTaskDB()
    friday_id = _seed_approved_draft(db, clients, draft_text="word " * 4000)

    dispatch.schedule_social_buffer_handler(
        friday_id, _envelope(friday_id, "schedule-social-buffer"), db
    )

    evidence = _card(gatekeeper)["evidence_summary"]
    draft_line = next(line for line in evidence.splitlines() if line.startswith("Draft:"))
    assert len(draft_line) < dispatch.APPROVAL_EXCERPT_CHARS + 100
    assert draft_line.endswith("…")


def test_the_carousel_csv_never_reaches_the_card(clients, gatekeeper):
    """Same reasoning as the review gate: the Canva bulk-create manifest
    is machine columns, and every cell in it is generated from slide text
    already in the excerpt."""
    db = FakeTaskDB()
    body = "[CAROUSEL]\nSlide 1: One number\n"
    csv = f"{dispatch.CAROUSEL_BULK_CSV_MARKER}\nslide_number,headline,image_ref\n1,x,"
    friday_id = _seed_approved_draft(
        db, clients, draft_task_type="draft-carousel-post", draft_text=body + csv
    )

    dispatch.schedule_social_buffer_handler(
        friday_id, _envelope(friday_id, "schedule-social-buffer"), db
    )

    evidence = _card(gatekeeper)["evidence_summary"]
    assert "One number" in evidence
    assert dispatch.CAROUSEL_BULK_CSV_MARKER not in evidence
    assert "image_ref" not in evidence


def test_the_newsletter_keeps_its_send_not_wired_caveat(clients, gatekeeper):
    """The single most important thing an approver of that card needs to
    know, and the one thing not derivable from the lineage."""
    db = FakeTaskDB()
    draft_id = _seed_approved_draft(db, clients)
    gate_ids = (db.get_task(draft_id) or {}).get("depends_on") or []
    publish_id = str(uuid.uuid4())
    db.seed(publish_id, "publish-newsletter", depends_on=gate_ids)

    dispatch.publish_newsletter_handler(
        publish_id, _envelope(publish_id, "publish-newsletter"), db
    )

    assert "send NOT yet wired" in _card(gatekeeper)["preview_title"]


def test_an_unreadable_excerpt_does_not_fail_the_approval(clients, gatekeeper, monkeypatch):
    """The asset is already reviewed and the hash is already bound. A card
    missing its excerpt is worse than one with it; an approval that dead-
    letters because the excerpt could not be fetched is worse still."""
    db = FakeTaskDB()
    friday_id = _seed_approved_draft(db, clients)

    def _boom(_asset_id):
        raise RuntimeError("vault unreachable")

    monkeypatch.setattr(clients, "get_asset", _boom)

    dispatch.schedule_social_buffer_handler(
        friday_id, _envelope(friday_id, "schedule-social-buffer"), db
    )

    evidence = _card(gatekeeper)["evidence_summary"]
    assert "Draft:" not in evidence
    assert "Reporting cycles fell from nine days to two" in evidence
    assert db.get_task(friday_id)["state"] == "completed"
