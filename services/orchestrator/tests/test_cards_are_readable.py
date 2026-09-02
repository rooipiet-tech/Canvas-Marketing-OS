"""Tests for the post-v1 opportunity_cards columns (F-CARDS-WRITE-ONLY).

score-signals wrote a card per signal, and the card carried a title and a
number. Nothing could use it: a person reading one in the console had a
headline with no source to check it against, and the weekly planner --
the one consumer that wanted this data -- had to re-derive every score
from raw signal payloads because the field it selects on, pillar, was not
on the card at all.

pillar, so_what, source_url and confidence were added to the frozen v1
vault schema as nullable columns (baseline refreshed deliberately, see
scripts/validate_contracts.py --write-baseline). These tests hold down
both halves: that score-signals fills them in, and that the weekly loop
now reads the daily loop's actual output instead of recomputing its own
opinion of it.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from orchestrator import dispatch
from tests.fakes import FakeVaultClient, patch_dispatch_clients
from tests.test_dispatch import FakeTaskDB, _envelope

FABRIC_URL = "https://learn.microsoft.com/en-us/fabric/get-started/whats-new"
MONEYWEB_URL = "https://www.moneyweb.co.za/feed/"


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


# ---------------------------------------------------------------------
# What a card now carries
# ---------------------------------------------------------------------


def test_a_card_carries_the_evidence_behind_its_own_score(clients):
    """The whole point: a card is readable on its own terms. A headline
    and a number is a claim; a headline, a source and a confidence is a
    claim somebody can check."""
    db = FakeTaskDB()
    _run_ingest_then_score(db)

    for card in clients._opportunity_cards.values():
        assert card["pillar"]
        assert card["so_what"]
        assert card["source_url"].startswith("https://")
        assert card["confidence"] in dispatch.CONFIDENCE_SCORES


def test_a_cards_confidence_is_the_input_its_score_was_computed_from(clients):
    """Carrying both makes a score auditable rather than something to be
    taken on trust."""
    db = FakeTaskDB()
    _run_ingest_then_score(db)

    for card in clients._opportunity_cards.values():
        assert card["score"] == dispatch.CONFIDENCE_SCORES[card["confidence"]]


def test_the_ranking_and_the_card_agree_about_every_signal(clients):
    db = FakeTaskDB()
    score_id = _run_ingest_then_score(db)

    ranking = db.get_result_ref(score_id)["ranking"]
    cards = {card["title"]: card for card in clients._opportunity_cards.values()}
    for item in ranking:
        card = cards[item["headline"]]
        assert card["source_url"] == item["source_url"]
        assert card["pillar"] == item["pillar"]
        assert card["so_what"] == item["so_what"]


# ---------------------------------------------------------------------
# Reading them back
# ---------------------------------------------------------------------


def _card(pillar: str, score: float, *, age_days: int = 0, **overrides) -> dict:
    card = {
        "id": str(uuid.uuid4()),
        "title": f"{pillar} headline",
        "so_what": "why it matters",
        "source_url": FABRIC_URL,
        "pillar": pillar,
        "confidence": "high",
        "score": score,
        "created_at": (
            datetime.now(timezone.utc) - timedelta(days=age_days)
        ).isoformat(),
    }
    card.update(overrides)
    return card


def _legacy_card() -> dict:
    """A card as score-signals wrote them before the columns existed."""
    card = _card("Fabric-native", 0.9)
    card.update({"title": "legacy card", "pillar": None, "so_what": None})
    return card


def _vault_with_cards(*cards: dict) -> FakeVaultClient:
    vault = FakeVaultClient()
    for card in cards:
        vault._opportunity_cards[card["id"]] = card
    return vault


def test_the_weekly_loop_reads_the_score_the_daily_loop_recorded():
    """Not a fresh opinion of it. Re-scoring here would let the weekly
    plan silently disagree with the brief that was published."""
    vault = _vault_with_cards(_card("Fabric-native", 0.77))

    scored = dispatch._recent_scored_signals(vault)

    assert [item["score"] for item in scored] == [0.77]
    assert scored[0]["so_what"] == "why it matters"
    assert scored[0]["source_url"] == FABRIC_URL


def test_cards_outside_the_window_do_not_vote():
    vault = _vault_with_cards(
        _card("Fabric-native", 0.8, age_days=1),
        _card("Finance-grade trust", 0.9, age_days=30),
    )

    scored = dispatch._recent_scored_signals(vault)

    assert [item["pillar"] for item in scored] == ["Fabric-native"]


def test_a_card_with_no_pillar_is_skipped_rather_than_guessed_at():
    """Cards written before the pillar column existed. Bucketing one under
    a guess would be worse than not counting it."""
    vault = _vault_with_cards(
        _card("Fabric-native", 0.5),
        _legacy_card(),
    )

    scored = dispatch._recent_scored_signals(vault)

    assert [item["headline"] for item in scored] == ["Fabric-native headline"]


def test_cards_pick_the_pillar_end_to_end():
    vault = _vault_with_cards(
        _card("Fabric-native", 0.5),
        _card("Finance-grade trust", 0.8),
    )

    scored = dispatch._recent_scored_signals(vault)

    assert dispatch._top_pillar(scored) == "Finance-grade trust"


# ---------------------------------------------------------------------
# The fallback
# ---------------------------------------------------------------------


def test_no_usable_cards_falls_back_to_re_deriving_from_signals():
    """A Vault upgraded mid-week holds a mix, and cards written before the
    columns existed carry no pillar. Re-deriving keeps planning on the
    evidence rather than reporting a quiet week that was not quiet."""
    vault = FakeVaultClient()
    vault._signals[str(uuid.uuid4())] = {
        "signal_type": "market_signal_batch",
        "received_at": datetime.now(timezone.utc).isoformat(),
        "payload": {
            "topic": "t",
            "signals": [
                {
                    "headline": "from the signal batch",
                    "so_what": "why",
                    "source_url": MONEYWEB_URL,
                    "pillar": "Fabric-native",
                    "confidence": "high",
                }
            ],
        },
    }

    scored = dispatch._recent_scored_signals(vault)

    assert [item["headline"] for item in scored] == ["from the signal batch"]
    assert scored[0]["score"] == dispatch.CONFIDENCE_SCORES["high"]


def test_an_unreachable_card_listing_falls_back_rather_than_raising(caplog):
    class Broken(FakeVaultClient):
        def list_opportunity_cards(self, *, limit: int = 100):
            raise RuntimeError("vault down")

    with caplog.at_level("WARNING"):
        scored = dispatch._recent_scored_signals(Broken())

    assert scored == []
    assert "recent_cards_unavailable" in caplog.text


def test_a_policy_it_cannot_read_degrades_planning_rather_than_raising(
    tmp_path, monkeypatch, caplog
):
    """_recent_scored_signals' contract is that it never raises. A bad
    policy is loud where it should be -- score-signals, every morning --
    not here."""
    shared = tmp_path / "_shared"
    shared.mkdir(parents=True)
    (shared / "scoring-policy.yaml").write_text(
        "pillar_weights:\n  Nonexistent pillar: 2.0\n", encoding="utf-8"
    )
    monkeypatch.setenv("FUNCTIONS_DIR", str(tmp_path))

    vault = FakeVaultClient()
    vault._signals[str(uuid.uuid4())] = {
        "signal_type": "market_signal_batch",
        "received_at": datetime.now(timezone.utc).isoformat(),
        "payload": {
            "signals": [
                {
                    "headline": "h",
                    "so_what": "w",
                    "source_url": MONEYWEB_URL,
                    "pillar": "Fabric-native",
                    "confidence": "high",
                }
            ]
        },
    }

    with caplog.at_level("WARNING"):
        scored = dispatch._recent_scored_signals(vault)

    assert [item["score"] for item in scored] == [dispatch.CONFIDENCE_SCORES["high"]]
    assert "scoring_policy_unreadable" in caplog.text


# ---------------------------------------------------------------------
# The contract itself
# ---------------------------------------------------------------------


def test_the_frozen_schema_declares_all_four_columns():
    from orchestrator.config import functions_dir

    schema = (
        functions_dir().parent / "contracts" / "vault-schema" / "schema.sql"
    ).read_text(encoding="utf-8")
    table = schema.split("CREATE TABLE IF NOT EXISTS opportunity_cards")[1].split(");")[0]

    for column in ("pillar", "so_what", "source_url", "confidence"):
        assert f"{column} " in table
        # The ALTERs are what get the column onto an ALREADY-DEPLOYED
        # database: CREATE TABLE IF NOT EXISTS is a no-op once the table
        # exists, so without these the columns ship to fresh databases only.
        assert f"ADD COLUMN IF NOT EXISTS {column} text;" in schema
