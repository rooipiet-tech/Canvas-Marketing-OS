"""Tests for the scoring policy (F-SCORING-POLICY-HARDCODED).

score-signals shipped with its rule hardcoded: function 09's confidence,
mapped to a number, and nothing else. Its own comment said where the rest
belonged -- "when that policy exists, it belongs in reviewable YAML beside
the scan profiles" -- so functions/_shared/scoring-policy.yaml is now that
file, and this is what holds it down.

Two properties matter more than any single rule here. First, THE SHIPPED
DEFAULTS CHANGE NOTHING: the file landed neutral, and a test says so, so
the diff that first makes the daily loop behave differently is the diff
that writes a real policy rather than the one that made policy possible.
Second, a policy that cannot be honoured is REFUSED rather than ignored --
a typo'd pillar name is somebody trying to change what the loop considers
important and failing silently, which is worse than not being able to.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import yaml
from orchestrator import dispatch
from orchestrator.config import functions_dir
from tests.fakes import patch_dispatch_clients
from tests.test_dispatch import FakeTaskDB, _envelope

FABRIC_URL = "https://learn.microsoft.com/en-us/fabric/get-started/whats-new"
MONEYWEB_URL = "https://www.moneyweb.co.za/feed/"
BUSINESSTECH_URL = "https://businesstech.co.za/news/feed/"

POLICY_PATH = functions_dir().joinpath(*dispatch.SCORING_POLICY_PATH)


def _signal(headline: str, url: str, confidence: str, pillar: str = "Fabric-native") -> dict:
    return {
        "headline": headline,
        "so_what": "why it matters",
        "source_url": url,
        "pillar": pillar,
        "confidence": confidence,
    }


def _batch(*signals: dict[str, Any]) -> dict[str, Any]:
    return {
        "topic": "test topic",
        "horizon_days": 30,
        "summary": "s" * 60,
        "signals": list(signals),
    }


def _write_policy(tmp_path, monkeypatch, policy: dict[str, Any]) -> None:
    """Point FUNCTIONS_DIR at a scratch tree carrying only this policy.

    functions_dir() is read at call time precisely so this works; nothing
    in dispatch resolves the path at import.
    """
    shared = tmp_path / "_shared"
    shared.mkdir(parents=True, exist_ok=True)
    (shared / "scoring-policy.yaml").write_text(yaml.safe_dump(policy), encoding="utf-8")
    monkeypatch.setenv("FUNCTIONS_DIR", str(tmp_path))


# ---------------------------------------------------------------------
# The shipped file
# ---------------------------------------------------------------------


def test_the_shipped_policy_reproduces_the_previous_behaviour_exactly():
    """The whole point of landing this file neutral. If this test has to
    change, the change belongs in its own reviewed commit."""
    policy = dispatch._load_scoring_policy()

    assert policy.confidence_weights == dispatch.CONFIDENCE_SCORES
    assert policy.unknown_confidence == dispatch.UNKNOWN_CONFIDENCE_SCORE
    assert policy.pillar_weights == {}
    assert policy.top_n is None
    assert policy.minimum_score is None
    assert policy.filters is False


def test_the_shipped_policy_scores_what_the_hardcoded_rule_scored():
    policy = dispatch._load_scoring_policy()

    for confidence, expected in (("high", 0.8), ("medium", 0.5), ("low", 0.25)):
        signal = _signal("h", FABRIC_URL, confidence)
        assert dispatch._score_signal(signal, policy) == expected
        assert dispatch._score_signal(signal) == expected


def test_the_shipped_policy_only_names_function_09s_own_enums():
    """A weight for a pillar function 09 cannot emit would never apply."""
    raw = yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8"))

    assert set(raw["confidence_weights"]) == set(dispatch.CONFIDENCE_SCORES)
    assert set(raw["pillar_weights"] or {}) <= set(dispatch.CONTENT_PILLARS)


def test_the_policy_file_is_staged_into_the_image():
    """Same failure mode the scan-profiles COPY exists to prevent: an
    unstaged file degrades to defaults silently instead of failing."""
    dockerfile = (
        functions_dir().parent / "services" / "orchestrator" / "Dockerfile"
    ).read_text(encoding="utf-8")
    assert "COPY functions/_shared/scoring-policy.yaml" in dockerfile


# ---------------------------------------------------------------------
# Loading: refuse what cannot be honoured
# ---------------------------------------------------------------------


def test_a_missing_policy_file_degrades_to_the_defaults(tmp_path, monkeypatch, caplog):
    """An image built before this file existed must keep scoring."""
    monkeypatch.setenv("FUNCTIONS_DIR", str(tmp_path))

    with caplog.at_level("WARNING"):
        policy = dispatch._load_scoring_policy()

    assert policy.confidence_weights == dispatch.CONFIDENCE_SCORES
    assert "scoring_policy_absent" in caplog.text


def test_a_typod_pillar_is_refused_not_ignored(tmp_path, monkeypatch):
    _write_policy(tmp_path, monkeypatch, {"pillar_weights": {"Fabric native": 1.5}})

    with pytest.raises(dispatch.DispatchError) as excinfo:
        dispatch._load_scoring_policy()

    assert "Fabric native" in str(excinfo.value)
    assert "scoring-policy.yaml" in str(excinfo.value)


def test_a_confidence_key_function_09_never_emits_is_refused(tmp_path, monkeypatch):
    _write_policy(tmp_path, monkeypatch, {"confidence_weights": {"high": 1.0, "certain": 1.0}})

    with pytest.raises(dispatch.DispatchError) as excinfo:
        dispatch._load_scoring_policy()

    assert "certain" in str(excinfo.value)


def test_a_top_n_below_one_is_refused(tmp_path, monkeypatch):
    """top_n: 0 would render a signal-less brief; say so at load rather
    than producing one."""
    _write_policy(tmp_path, monkeypatch, {"selection": {"top_n": 0}})

    with pytest.raises(dispatch.DispatchError):
        dispatch._load_scoring_policy()


def test_an_empty_policy_file_is_the_defaults(tmp_path, monkeypatch):
    _write_policy(tmp_path, monkeypatch, {"version": 1})

    policy = dispatch._load_scoring_policy()

    assert policy.confidence_weights == dispatch.CONFIDENCE_SCORES
    assert policy.filters is False


# ---------------------------------------------------------------------
# Pillar weighting
# ---------------------------------------------------------------------


def test_a_weighted_pillar_outranks_an_unweighted_one_of_equal_confidence():
    policy = dispatch.ScoringPolicy(pillar_weights={"Finance-grade trust": 1.25})
    batch = _batch(
        _signal("fabric", FABRIC_URL, "medium", "Fabric-native"),
        _signal("finance", MONEYWEB_URL, "medium", "Finance-grade trust"),
    )

    ranked = dispatch._rank_signals(batch, policy)

    assert ranked[0]["headline"] == "finance"
    assert ranked[0]["score"] > ranked[1]["score"]


def test_a_promoted_medium_cannot_outrank_a_high_by_arithmetic_alone():
    """The clamp. Weighting a pillar says "look here first", not "treat
    thin evidence as strong"."""
    policy = dispatch.ScoringPolicy(pillar_weights={"Finance-grade trust": 10.0})
    batch = _batch(
        _signal("promoted medium", MONEYWEB_URL, "medium", "Finance-grade trust"),
        _signal("plain high", FABRIC_URL, "high", "Fabric-native"),
    )

    ranked = dispatch._rank_signals(batch, policy)

    assert ranked[0]["score"] <= 1.0
    assert all(item["score"] <= 1.0 for item in ranked)


def test_demoting_a_pillar_pushes_it_down_without_dropping_it():
    policy = dispatch.ScoringPolicy(pillar_weights={"Fabric-native": 0.5})
    batch = _batch(
        _signal("fabric high", FABRIC_URL, "high", "Fabric-native"),
        _signal("finance medium", MONEYWEB_URL, "medium", "Finance-grade trust"),
    )

    ranked = dispatch._rank_signals(batch, policy)

    assert ranked[0]["headline"] == "finance medium"
    assert len(ranked) == 2


# ---------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------


def test_no_policy_selects_everything():
    batch = _batch(
        _signal("a", FABRIC_URL, "high"),
        _signal("b", MONEYWEB_URL, "low"),
    )

    ranked = dispatch._rank_signals(batch)

    assert all(item["selected"] for item in ranked)


def test_top_n_selects_the_best_scored_not_the_first_emitted():
    policy = dispatch.ScoringPolicy(top_n=1)
    batch = _batch(
        _signal("emitted first, scores low", FABRIC_URL, "low"),
        _signal("emitted second, scores high", MONEYWEB_URL, "high"),
    )

    ranked = dispatch._rank_signals(batch, policy)

    selected = [item["headline"] for item in ranked if item["selected"]]
    assert selected == ["emitted second, scores high"]


def test_a_minimum_score_drops_everything_beneath_it():
    policy = dispatch.ScoringPolicy(minimum_score=0.5)
    batch = _batch(
        _signal("high", FABRIC_URL, "high"),
        _signal("medium", MONEYWEB_URL, "medium"),
        _signal("low", BUSINESSTECH_URL, "low"),
    )

    ranked = dispatch._rank_signals(batch, policy)

    assert [item["headline"] for item in ranked if item["selected"]] == ["high", "medium"]


def test_a_floor_that_would_empty_the_batch_still_keeps_the_best_one():
    """A brief with no signals is a failure to report, not a report."""
    policy = dispatch.ScoringPolicy(minimum_score=0.99)
    batch = _batch(
        _signal("best available", FABRIC_URL, "medium"),
        _signal("worse", MONEYWEB_URL, "low"),
    )

    ranked = dispatch._rank_signals(batch, policy)

    assert [item["headline"] for item in ranked if item["selected"]] == ["best available"]


def test_both_cuts_apply_together():
    policy = dispatch.ScoringPolicy(top_n=3, minimum_score=0.5)
    batch = _batch(
        _signal("high", FABRIC_URL, "high"),
        _signal("medium", MONEYWEB_URL, "medium"),
        _signal("low", BUSINESSTECH_URL, "low"),
    )

    ranked = dispatch._rank_signals(batch, policy)

    assert [item["headline"] for item in ranked if item["selected"]] == ["high", "medium"]


# ---------------------------------------------------------------------
# What the brief shows
# ---------------------------------------------------------------------


def test_a_held_back_signal_is_absent_from_the_brief_but_the_cut_is_stated():
    batch = _batch(
        _signal("shown", FABRIC_URL, "high"),
        _signal("held back", MONEYWEB_URL, "low"),
    )
    ranking = dispatch._rank_signals(batch, dispatch.ScoringPolicy(top_n=1))

    full_body, _executive = dispatch._render_brief("t", batch, ranking)

    assert "shown" in full_body
    assert "held back" not in full_body
    assert "1 further signal(s)" in full_body
    assert "scoring-policy.yaml" in full_body


def test_a_brief_with_no_cut_says_nothing_about_one():
    batch = _batch(
        _signal("a", FABRIC_URL, "high"),
        _signal("b", MONEYWEB_URL, "low"),
    )
    ranking = dispatch._rank_signals(batch)

    full_body, _executive = dispatch._render_brief("t", batch, ranking)

    assert "further signal(s)" not in full_body
    assert "a" in full_body and "b" in full_body


def test_a_ranking_from_before_selection_existed_still_renders_everything():
    """Pre-policy result_refs carry no `selected` key. A brief rendered
    from one must show the whole batch, not silently hide all of it."""
    batch = _batch(
        _signal("first", FABRIC_URL, "high"),
        _signal("second", MONEYWEB_URL, "low"),
    )
    legacy_ranking = [
        {"source_url": FABRIC_URL, "score": 0.8},
        {"source_url": MONEYWEB_URL, "score": 0.25},
    ]

    full_body, _executive = dispatch._render_brief("t", batch, legacy_ranking)

    assert "first" in full_body and "second" in full_body
    assert "further signal(s)" not in full_body


# ---------------------------------------------------------------------
# End to end through the handler
# ---------------------------------------------------------------------


@pytest.fixture()
def clients(monkeypatch):
    return patch_dispatch_clients(monkeypatch)


def _run_ingest_then_score(db: FakeTaskDB) -> str:
    ingest_id = str(uuid.uuid4())
    db.seed(ingest_id, "ingest-signals")
    dispatch.ingest_signals_handler(ingest_id, _envelope(ingest_id, "ingest-signals"), db)

    score_id = str(uuid.uuid4())
    db.seed(score_id, "score-signals", depends_on=[ingest_id])
    dispatch.score_signals_handler(score_id, _envelope(score_id, "score-signals"), db)
    return score_id


def test_a_held_back_signal_still_gets_an_opportunity_card(monkeypatch, clients):
    """The Vault keeps the whole scan regardless of what the brief shows,
    so a narrowed brief never costs the record.

    Patches the loaded policy rather than the functions tree: FUNCTIONS_DIR
    also resolves the prompts and schemas this handler reads, so pointing
    it at a scratch directory would test the fixture, not the cut.
    """
    monkeypatch.setattr(
        dispatch, "_load_scoring_policy", lambda: dispatch.ScoringPolicy(top_n=1)
    )

    db = FakeTaskDB()
    score_id = _run_ingest_then_score(db)

    ref = db.get_result_ref(score_id)
    selected = [item for item in ref["ranking"] if item["selected"]]
    assert len(selected) == 1
    # Every signal scored, not just the selected one.
    assert len(ref["opportunity_card_ids"]) == len(ref["ranking"]) == 3
    assert len(clients._opportunity_cards) == 3


def test_the_brief_shows_only_what_the_policy_selected(monkeypatch, clients):
    monkeypatch.setattr(
        dispatch, "_load_scoring_policy", lambda: dispatch.ScoringPolicy(top_n=1)
    )
    db = FakeTaskDB()
    score_id = _run_ingest_then_score(db)

    draft_id = str(uuid.uuid4())
    db.seed(draft_id, "draft-brief", depends_on=[score_id])
    dispatch.draft_brief_handler(draft_id, _envelope(draft_id, "draft-brief"), db)

    full = next(
        brief for brief in clients._briefs.values() if brief["title"].startswith("Morning")
    )
    assert "2 further signal(s)" in full["body"]


def test_the_ranking_records_selection_for_the_brief_to_read(clients):
    db = FakeTaskDB()
    score_id = _run_ingest_then_score(db)

    ranking = db.get_result_ref(score_id)["ranking"]
    assert all("selected" in item for item in ranking)
    # Shipped policy has no cut, so everything is selected.
    assert all(item["selected"] for item in ranking)
