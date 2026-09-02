"""The daily loop's QA gate needs the same false-positive backstop the
weekly one has.

F-DAILY-QA-NO-BACKSTOP, live: deploy-pipeline run 4 got the whole daily
loop working -- the scan produced 3 real signals, scoring ran, the brief
drafted -- and then qa-review blocked on ["sa-english-spelling"], taking
draft-content, qa-review, request-approval and publish-brief down with it.

brand_rules.py exists precisely for that code. Its docstring records the
run it was written after: all six weekly drafts blocked on
sa-english-spelling/unsupported-claim, every one re-checked by hand
against the same deterministic logic and found CLEAN -- a hallucination
by the QA model, not a drafting defect. It was then wired into
_single_draft_qa_review and the retry loop.

qa_review_handler -- the DAILY loop's gate -- never got it. The handler's
own comment said so out loud ("This path runs no reconcile_violations"),
which is how a documented protection ends up covering one of the two
paths that need it.

Reconciliation is safe here for the reason brand_rules' docstring gives:
it only ever REMOVES sa-english-spelling and unsupported-claim, never
adds them, and never touches the other four checks. These tests pin both
directions -- a hallucinated code is dropped, a real one still blocks --
because a backstop that swallowed genuine findings would be worse than
none.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from orchestrator import dispatch
from tests.fakes import patch_dispatch_clients
from tests.test_dispatch import FakeTaskDB

# Reuse the QA harness rather than rebuilding it: _VerdictGatewayClient is
# how every other QA test supplies a canned verdict, and _run_review is
# already parameterised by task type.
from tests.test_dispatch_qa_verdict import _run_review

CLEAN_SA_TEXT = (
    "Our organisation helps finance teams recognise the value of a single "
    "centralised ledger, and we analyse consolidation behaviour across entities."
)
US_SPELLING_TEXT = (
    "Our organization helps finance teams recognize the value of a single "
    "centralized ledger."
)


@pytest.fixture()
def clients(monkeypatch):
    return patch_dispatch_clients(monkeypatch)


def _seed_daily_qa(db: FakeTaskDB, vault: Any, draft_text: str) -> str:
    """A completed draft-content plus the DAILY loop's qa-review task.

    Deliberately the daily shape (`qa-review` over a `draft-content`
    ancestor), not the weekly `qa-review-brand-steward` the other QA tests
    use -- the daily handler is the one that had no backstop.
    """
    draft_id, qa_id = str(uuid.uuid4()), str(uuid.uuid4())
    db.seed(draft_id, "draft-content")
    db.seed(qa_id, "qa-review", depends_on=[draft_id])
    asset = vault.create_asset(
        asset_type="social_post",
        agent_run_id=None,
        campaign_id=None,
        function_id=dispatch.FUNCTION_ID_39,
        content_bytes=draft_text.encode("utf-8"),
        approval_state="draft",
    )
    db.set_result_ref(
        draft_id,
        {
            "vault_asset_id": asset["id"],
            "content_hash": asset["content_hash"],
            "pillar": "Consolidation at scale",
        },
    )
    db.transition(
        draft_id, dispatch.TaskStateEnum.COMPLETED, dispatch.TransitionReason.COMPLETED
    )
    return qa_id


def test_a_hallucinated_spelling_violation_is_dropped(clients, monkeypatch, caplog):
    """The live failure: the model asserts sa-english-spelling on text that
    contains no US spelling at all."""
    db = FakeTaskDB()
    qa_id = _seed_daily_qa(db, clients, CLEAN_SA_TEXT)

    with caplog.at_level("WARNING"):
        _run_review(
            monkeypatch,
            db,
            {"pass": False, "violations": ["sa-english-spelling"], "notes": "n"},
            qa_id,
            task_type="qa-review",
        )

    # The passing branch writes a different ref shape entirely -- there is
    # no "violations" key on a pass, which is itself the proof the block
    # branch was never taken.
    ref = db.get_result_ref(qa_id)
    assert ref["pass"] is True
    assert "violations" not in ref
    assert db.get_task(qa_id)["state"] == "completed"
    assert "qa_review_false_positive_dropped" in caplog.text
    # And critically NOT re-blocked under a different label: without the
    # `not dropped` condition this lands on
    # verdict-declared-failure-without-code instead.
    assert "qa_verdict_failed_without_violation_code" not in caplog.text


def test_a_real_spelling_violation_still_blocks(clients, monkeypatch):
    """The backstop must not swallow a genuine finding -- that would be
    worse than having none."""
    db = FakeTaskDB()
    qa_id = _seed_daily_qa(db, clients, US_SPELLING_TEXT)

    _run_review(
        monkeypatch,
        db,
        {"pass": False, "violations": ["sa-english-spelling"], "notes": "n"},
        qa_id,
        task_type="qa-review",
    )

    ref = db.get_result_ref(qa_id)
    assert ref["violations"] == ["sa-english-spelling"]
    assert ref["pass"] is False


def test_other_violation_codes_are_never_reconciled_away(clients, monkeypatch):
    """Reconciliation touches two codes only. Anything the model catches in
    the other four checks must survive untouched, even on clean text."""
    db = FakeTaskDB()
    qa_id = _seed_daily_qa(db, clients, CLEAN_SA_TEXT)

    _run_review(
        monkeypatch,
        db,
        {"pass": False, "violations": ["sa-english-spelling", "missing-cta"], "notes": "n"},
        qa_id,
        task_type="qa-review",
    )

    ref = db.get_result_ref(qa_id)
    assert ref["violations"] == ["missing-cta"]
    assert ref["pass"] is False
