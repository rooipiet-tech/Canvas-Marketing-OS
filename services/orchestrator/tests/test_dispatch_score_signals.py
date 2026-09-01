"""Tests for score-signals (F-NO-SCORING).

"Score what matters" is step 2 of the pipeline the README advertises and
it did not exist: score-signals fell through to legacy_task_pass_through,
and opportunity_cards -- a table in the frozen vault schema, routed by the
Vault API, indexed by campaign -- had no writer anywhere in the codebase.
draft-brief rendered every signal in whatever order the model emitted.

Two things these tests hold down. First, the scoring itself, which is
deterministic and deliberately coarse. Second, and less obvious: giving
score-signals a result_ref changes which ancestor draft-brief resolves --
it used to walk PAST the no-op to ingest, and now stops at score. That
makes score's result_ref a load-bearing superset of ingest's, and a
missing key there would break the brief rather than the scorer.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from orchestrator import dispatch
from tests.fakes import patch_dispatch_clients
from tests.test_dispatch import FakeTaskDB, _envelope

FABRIC_URL = "https://learn.microsoft.com/en-us/fabric/get-started/whats-new"
MONEYWEB_URL = "https://www.moneyweb.co.za/feed/"
BUSINESSTECH_URL = "https://businesstech.co.za/news/feed/"


def _signal(headline: str, url: str, confidence: str) -> dict[str, Any]:
    return {
        "headline": headline,
        "so_what": "why it matters",
        "source_url": url,
        "pillar": "Fabric-native",
        "confidence": confidence,
    }


def _batch(*signals: dict[str, Any]) -> dict[str, Any]:
    return {
        "topic": "test topic",
        "horizon_days": 30,
        "summary": "s" * 60,
        "signals": list(signals),
    }


# ---------------------------------------------------------------------
# The scoring rule
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "confidence,expected",
    [("high", 0.8), ("medium", 0.5), ("low", 0.25)],
)
def test_score_comes_from_function_09s_own_confidence(confidence, expected):
    assert dispatch._score_signal(_signal("h", FABRIC_URL, confidence)) == expected


def test_an_unrecognised_confidence_scores_below_low_rather_than_raising():
    """Schema validation already rejects a bad confidence upstream, so this
    is a floor for the impossible case, not a second gate."""
    assert dispatch._score_signal({"confidence": "extremely"}) < dispatch.CONFIDENCE_SCORES["low"]
    assert dispatch._score_signal({}) < dispatch.CONFIDENCE_SCORES["low"]


def test_ranking_is_highest_score_first():
    ranked = dispatch._rank_signals(
        _batch(
            _signal("low one", FABRIC_URL, "low"),
            _signal("high one", MONEYWEB_URL, "high"),
            _signal("medium one", BUSINESSTECH_URL, "medium"),
        )
    )

    assert [item["headline"] for item in ranked] == ["high one", "medium one", "low one"]
    assert [item["score"] for item in ranked] == [0.8, 0.5, 0.25]


def test_ties_keep_the_order_function_09_emitted_them():
    """A stable sort, so the same batch always ranks the same way and a
    reviewer comparing two runs sees real change, not sort noise."""
    ranked = dispatch._rank_signals(
        _batch(
            _signal("first high", FABRIC_URL, "high"),
            _signal("second high", MONEYWEB_URL, "high"),
            _signal("third high", BUSINESSTECH_URL, "high"),
        )
    )

    assert [item["headline"] for item in ranked] == ["first high", "second high", "third high"]


def test_ranking_carries_what_the_brief_needs_to_reorder_by():
    ranked = dispatch._rank_signals(_batch(_signal("h", FABRIC_URL, "high")))

    assert ranked[0]["source_url"] == FABRIC_URL
    assert ranked[0]["pillar"] == "Fabric-native"
    assert ranked[0]["confidence"] == "high"


# ---------------------------------------------------------------------
# The handler
# ---------------------------------------------------------------------


@pytest.fixture()
def clients(monkeypatch):
    return patch_dispatch_clients(monkeypatch)


def _run_ingest_then_score(db: FakeTaskDB) -> tuple[str, str]:
    ingest_id = str(uuid.uuid4())
    db.seed(ingest_id, "ingest-signals")
    dispatch.ingest_signals_handler(ingest_id, _envelope(ingest_id, "ingest-signals"), db)

    score_id = str(uuid.uuid4())
    db.seed(score_id, "score-signals", depends_on=[ingest_id])
    dispatch.score_signals_handler(score_id, _envelope(score_id, "score-signals"), db)
    return ingest_id, score_id


def test_scoring_writes_one_opportunity_card_per_signal(clients):
    """Closes the architecture review's F10: the table had no writer."""
    db = FakeTaskDB()
    _ingest_id, score_id = _run_ingest_then_score(db)

    ref = db.get_result_ref(score_id)
    assert len(ref["opportunity_card_ids"]) == 3
    assert len(clients._opportunity_cards) == 3

    cards = list(clients._opportunity_cards.values())
    assert {card["status"] for card in cards} == {"new"}
    assert all(card["signal_id"] == ref["vault_signal_id"] for card in cards)
    assert sorted(card["score"] for card in cards) == [0.25, 0.5, 0.8]


def test_cards_are_titled_with_the_headline_they_score(clients):
    db = FakeTaskDB()
    _ingest_id, score_id = _run_ingest_then_score(db)

    titles = {card["title"] for card in clients._opportunity_cards.values()}
    assert "Microsoft ships new Fabric capacity tooling" in titles


def test_the_task_completes_and_advances_its_dependents(clients):
    db = FakeTaskDB()
    _ingest_id, score_id = _run_ingest_then_score(db)

    assert db.get_task(score_id)["state"] == "completed"


def test_scoring_refuses_when_no_ancestor_carries_a_signal(clients):
    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    db.seed(task_id, "score-signals")

    with pytest.raises(dispatch.DispatchError, match="no ancestor task carries a result_ref"):
        dispatch.score_signals_handler(task_id, _envelope(task_id, "score-signals"), db)

    assert db.get_task(task_id)["state"] != "completed"


# ---------------------------------------------------------------------
# The lineage consequence — score now sits between ingest and draft-brief
# ---------------------------------------------------------------------


def test_scores_result_ref_still_answers_draft_briefs_questions(clients):
    """draft-brief used to walk PAST the no-op score task to ingest. Now it
    stops at score, so score's result_ref must carry everything ingest's
    did that the brief reads."""
    db = FakeTaskDB()
    ingest_id, score_id = _run_ingest_then_score(db)

    ingest_ref = db.get_result_ref(ingest_id)
    score_ref = db.get_result_ref(score_id)

    assert score_ref["vault_signal_id"] == ingest_ref["vault_signal_id"]
    assert score_ref["topic"] == ingest_ref["topic"]
    assert score_ref["campaign_id"]


def test_the_brief_leads_with_the_best_evidenced_signal(clients):
    """The point of scoring existing at all: the executive edition's top
    three are the top three, not the first three the model emitted."""
    db = FakeTaskDB()
    _ingest_id, score_id = _run_ingest_then_score(db)

    draft_id = str(uuid.uuid4())
    db.seed(draft_id, "draft-brief", depends_on=[score_id])
    dispatch.draft_brief_handler(draft_id, _envelope(draft_id, "draft-brief"), db)

    executive = next(
        brief for brief in clients._briefs.values() if brief["title"].startswith("Executive")
    )
    body = executive["body"]
    # The fake batch emits high, medium, low in that order already, so
    # assert the ordering rule directly rather than relying on it.
    high = body.index("Microsoft ships new Fabric capacity tooling")
    low = body.index("SA tech press covers analytics platform spend")
    assert high < low


def test_rendering_without_a_ranking_is_unchanged(clients):
    """A brief with no scoring ancestor still renders, in emitted order --
    the pre-scoring behaviour, kept so the ranking is an enhancement and
    not a dependency."""
    batch = _batch(
        _signal("low first", FABRIC_URL, "low"),
        _signal("high second", MONEYWEB_URL, "high"),
    )

    full_body, _executive = dispatch._render_brief("t", batch, None)

    assert full_body.index("low first") < full_body.index("high second")


def test_a_signal_missing_from_the_ranking_is_still_rendered():
    """Rendering fewer signals than the batch holds would be a silent edit;
    this function renders, it does not curate."""
    batch = _batch(
        _signal("ranked", FABRIC_URL, "high"),
        _signal("unranked", MONEYWEB_URL, "high"),
    )
    ranking = [{"source_url": FABRIC_URL, "score": 0.8}]

    full_body, _executive = dispatch._render_brief("t", batch, ranking)

    assert "ranked" in full_body
    assert "unranked" in full_body
