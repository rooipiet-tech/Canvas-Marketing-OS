"""The scanners' dead tail.

All eleven fan-out scanners fed `dedupe-signal-cards`, and every task from
there down was unregistered -- falling through to
legacy_task_pass_through, which sets no result_ref and completes the task
"successfully" having done nothing:

    11 scanners -> dedupe -> strategize -> morning-brief-rollup
                                        -> executive-brief-rollup

So the scanners ran every morning, cost a model call each, wrote a card
batch into the Vault, and nothing read any of it. The morning brief a
person actually receives is built by draft-brief on the separate
ingest -> score -> draft path, which never sees a single scanner card.

`publish-brief` was dead the same way, and hid a second fault: the brief
was announced to Teams inside draft_brief_handler the moment it was
created, before the qa task had run at all.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
import yaml
from orchestrator import dispatch
from tests.fakes import patch_dispatch_clients
from tests.test_dispatch import FakeTaskDB, _envelope

LOOPS = Path(__file__).resolve().parents[1] / "loops"

# The nightly analytics loop is documentary: its own header records that
# the real execution mechanism is caj-analytics-nightly-ingest, a
# Container Apps Job, and that the file exists as registry metadata. Its
# task types are legitimately unhandled here.
DOCUMENTARY_LOOPS = {"nightly-analytics-ingest-loop"}


def _card(headline: str, url: str, **over: Any) -> dict[str, Any]:
    card = {
        "headline": headline,
        "so_what": "why this matters to the CFO office",
        "source_url": url,
        "card_type": "threat",
        "taxonomy": "pillar-defence",
        "evidence_grade": "strong",
        "confidence": "high",
    }
    card.update(over)
    return card


@pytest.fixture()
def clients(monkeypatch):
    return patch_dispatch_clients(monkeypatch)


def _seed_scanners(db: FakeTaskDB, vault: Any, batches: list[tuple[str, list[dict]]]) -> str:
    """One completed scanner task per batch, then the dedupe task."""
    scanner_ids = []
    for profile_id, cards in batches:
        task_id = str(uuid.uuid4())
        db.seed(task_id, "competitor-discovery-scan")
        signal = vault.create_signal(
            source=f"function-{profile_id}",
            signal_type=dispatch.CARD_BATCH_TYPE,
            payload={"topic": "t", "horizon_days": 30, "summary": "s" * 40, "cards": cards},
            campaign_id=None,
            function_id="10-competitor-discovery-scanner",
        )
        db.set_result_ref(
            task_id,
            {"status": "scanned", "vault_signal_id": signal["id"], "profile_id": profile_id},
        )
        db.transition(
            task_id, dispatch.TaskStateEnum.COMPLETED, dispatch.TransitionReason.COMPLETED
        )
        scanner_ids.append(task_id)

    dedupe_id = str(uuid.uuid4())
    db.seed(dedupe_id, "dedupe-signal-cards", depends_on=scanner_ids)
    return dedupe_id


def _run(db: FakeTaskDB, task_id: str, task_type: str) -> None:
    dispatch.DISPATCH_TABLE[task_type](task_id, _envelope(task_id, task_type), db)


# --- the wiring that was dead ---------------------------------------------


def test_every_loop_task_type_has_a_handler():
    """THE bug, as an invariant. An unregistered task_type does not fail:
    it falls through to legacy_task_pass_through and reports success
    having done nothing, so an entire branch of a loop can be dead while
    every run is green. Five daily-loop tasks were, including the one all
    eleven scanners feed."""
    registered = set(dispatch.DISPATCH_TABLE)
    dead: dict[str, list[str]] = {}
    for path in sorted(LOOPS.glob("*.yaml")):
        loop = yaml.safe_load(path.read_text(encoding="utf-8"))
        if loop["loop_id"] in DOCUMENTARY_LOOPS:
            continue
        missing = sorted({t["task_type"] for t in loop["tasks"]} - registered)
        if missing:
            dead[loop["loop_id"]] = missing
    assert not dead, f"loop tasks with no handler, silently doing nothing: {dead}"


# --- dedupe ----------------------------------------------------------------


def test_the_same_story_from_two_scanners_becomes_one_card(clients):
    """The whole reason to merge eleven batches rather than read one."""
    db = FakeTaskDB()
    url = "https://www.moneyweb.co.za/a"
    dedupe_id = _seed_scanners(
        db,
        clients,
        [
            ("competitor-discovery", [_card("Rival ships a BI add-on", url)]),
            ("fabric-ecosystem", [_card("Rival ships a BI add-on", url)]),
        ],
    )

    _run(db, dedupe_id, "dedupe-signal-cards")

    ref = db.get_result_ref(dedupe_id)
    assert ref["cards_in"] == 2
    assert ref["cards_out"] == 1
    assert ref["cards"][0]["seen_by"] == 2
    assert sorted(ref["cards"][0]["profiles"]) == ["competitor-discovery", "fabric-ecosystem"]


def test_the_same_story_under_two_urls_is_still_one_card(clients):
    """Two publications, one story: the headline catches what the url
    cannot."""
    db = FakeTaskDB()
    dedupe_id = _seed_scanners(
        db,
        clients,
        [
            ("a", [_card("Rival ships a BI add-on", "https://x.example/1")]),
            ("b", [_card("rival  ships a BI ADD-ON  ", "https://y.example/2")]),
        ],
    )

    _run(db, dedupe_id, "dedupe-signal-cards")

    assert db.get_result_ref(dedupe_id)["cards_out"] == 1


def test_distinct_stories_are_kept(clients):
    """Dedupe must not be a synonym for discard."""
    db = FakeTaskDB()
    dedupe_id = _seed_scanners(
        db,
        clients,
        [
            ("a", [_card("Rival ships a BI add-on", "https://x.example/1")]),
            ("b", [_card("Regulator opens a consultation", "https://y.example/2")]),
        ],
    )

    _run(db, dedupe_id, "dedupe-signal-cards")

    assert db.get_result_ref(dedupe_id)["cards_out"] == 2


def test_corroborated_cards_rank_first(clients):
    """A story three profiles found independently outranks one that one
    profile found, whatever order the scanners happened to run in."""
    db = FakeTaskDB()
    lone = _card("Single-source item", "https://x.example/1")
    shared = _card("Corroborated item", "https://y.example/2")
    dedupe_id = _seed_scanners(
        db, clients, [("a", [lone, shared]), ("b", [shared]), ("c", [shared])]
    )

    _run(db, dedupe_id, "dedupe-signal-cards")

    cards = db.get_result_ref(dedupe_id)["cards"]
    assert cards[0]["headline"] == "Corroborated item"
    assert cards[0]["seen_by"] == 3


def test_an_unconfigured_scanner_is_skipped_not_fatal(clients):
    """Eleven profiles still have no source urls, so most mornings some
    scanners complete as not_configured. Losing the scans that did run
    over the ones that could not is the wrong trade."""
    db = FakeTaskDB()
    dedupe_id = _seed_scanners(db, clients, [("a", [_card("Real item", "https://x.example/1")])])
    unconfigured = str(uuid.uuid4())
    db.seed(unconfigured, "vertical-scan-mining-industrial")
    db.set_result_ref(unconfigured, {"status": "not_configured", "profile_id": "mining"})
    db.transition(
        unconfigured, dispatch.TaskStateEnum.COMPLETED, dispatch.TransitionReason.COMPLETED
    )
    db.tasks[dedupe_id]["depends_on"].append(unconfigured)

    _run(db, dedupe_id, "dedupe-signal-cards")

    ref = db.get_result_ref(dedupe_id)
    assert ref["scanners_read"] == 1
    assert ref["cards_out"] == 1


def test_a_morning_with_no_cards_completes(clients):
    db = FakeTaskDB()
    dedupe_id = _seed_scanners(db, clients, [])

    _run(db, dedupe_id, "dedupe-signal-cards")

    assert db.get_task(dedupe_id)["state"] == "completed"
    assert db.get_result_ref(dedupe_id)["cards_out"] == 0


# --- strategist ------------------------------------------------------------


def _seed_plan(db: FakeTaskDB, clients, cards: list[dict]) -> tuple[str, str]:
    dedupe_id = _seed_scanners(db, clients, [("a", cards)] if cards else [])
    _run(db, dedupe_id, "dedupe-signal-cards")
    plan_id = str(uuid.uuid4())
    db.seed(plan_id, "competitive-response-strategize", depends_on=[dedupe_id])
    return dedupe_id, plan_id


def test_the_strategist_is_sent_its_declared_input(clients, monkeypatch):
    """Function 25's input is additionalProperties:false and has no
    `confidence`, which every scanner card carries -- so a deduped card
    cannot be forwarded verbatim, and neither can this handler's own
    seen_by/profiles."""
    sent: list[dict] = []
    from tests.fakes import FakeGatewayClient

    class _Recorder(FakeGatewayClient):
        def complete(self, **kw):
            if "Competitive Response Strategist" in kw["system_prompt"]:
                sent.append(json.loads(kw["user_content"]))
            return super().complete(**kw)

    monkeypatch.setattr(dispatch, "build_gateway_client", lambda: _Recorder())
    db = FakeTaskDB()
    _dedupe_id, plan_id = _seed_plan(db, clients, [_card("A competitor moved", "https://x.example/1")])

    _run(db, plan_id, "competitive-response-strategize")

    assert len(sent) == 1
    dispatch._validate_function_input(dispatch.FUNCTION_ID_25, sent[0])
    card = sent[0]["cards"][0]
    assert "confidence" not in card
    assert "seen_by" not in card
    assert "profiles" not in card


def test_the_strategist_declines_an_empty_morning(clients):
    """Ranking an empty list would mean inventing one."""
    db = FakeTaskDB()
    _dedupe_id, plan_id = _seed_plan(db, clients, [])

    _run(db, plan_id, "competitive-response-strategize")

    assert db.get_task(plan_id)["state"] == "completed"
    assert db.get_result_ref(plan_id)["status"] == "no_cards"


def test_the_plan_covers_the_cards_that_survived_dedupe(clients):
    db = FakeTaskDB()
    _dedupe_id, plan_id = _seed_plan(
        db,
        clients,
        [_card("A competitor moved", "https://x.example/1"), _card("A regulator consulted", "https://y.example/2")],
    )

    _run(db, plan_id, "competitive-response-strategize")

    plan = db.get_result_ref(plan_id)["response_plan"]
    assert {item["headline"] for item in plan} == {"A competitor moved", "A regulator consulted"}


# --- rollups ---------------------------------------------------------------


def _seed_rollup(db: FakeTaskDB, clients, cards: list[dict]) -> str:
    dedupe_id, plan_id = _seed_plan(db, clients, cards)
    _run(db, plan_id, "competitive-response-strategize")
    rollup_id = str(uuid.uuid4())
    db.seed(rollup_id, "morning-brief-rollup", depends_on=[dedupe_id, plan_id])
    return rollup_id


def test_the_brief_carries_the_cards_and_the_plan(clients):
    db = FakeTaskDB()
    rollup_id = _seed_rollup(db, clients, [_card("Rival ships an add-on", "https://x.example/1")])

    _run(db, rollup_id, "morning-brief-rollup")

    body = list(clients._briefs.values())[-1]["body"]
    assert "Rival ships an add-on" in body
    assert "## Response plan" in body
    assert "reassert-differentiation" in body
    assert db.get_result_ref(rollup_id)["card_count"] == 1


def test_corroboration_is_visible_to_the_reader(clients):
    """The one fact that exists only because eleven batches were merged."""
    db = FakeTaskDB()
    shared = _card("Corroborated item", "https://y.example/2")
    dedupe_id = _seed_scanners(db, clients, [("a", [shared]), ("b", [shared])])
    _run(db, dedupe_id, "dedupe-signal-cards")
    plan_id = str(uuid.uuid4())
    db.seed(plan_id, "competitive-response-strategize", depends_on=[dedupe_id])
    _run(db, plan_id, "competitive-response-strategize")
    rollup_id = str(uuid.uuid4())
    db.seed(rollup_id, "morning-brief-rollup", depends_on=[dedupe_id, plan_id])

    _run(db, rollup_id, "morning-brief-rollup")

    assert "seen by 2 scanners" in list(clients._briefs.values())[-1]["body"]


def test_an_empty_morning_says_so_rather_than_rendering_blank(clients):
    """An empty section reads like a formatting fault; a sentence reads
    like an answer."""
    db = FakeTaskDB()
    rollup_id = _seed_rollup(db, clients, [])

    _run(db, rollup_id, "morning-brief-rollup")

    body = list(clients._briefs.values())[-1]["body"]
    assert "No cards." in body
    assert "the strategist had no cards to rank" in body


def test_the_executive_edition_reuses_the_rollup_body(clients):
    """Two renderings of one morning that could disagree is the drift
    this work has been removing."""
    db = FakeTaskDB()
    rollup_id = _seed_rollup(db, clients, [_card("A competitor moved", "https://x.example/1")])
    _run(db, rollup_id, "morning-brief-rollup")
    exec_id = str(uuid.uuid4())
    db.seed(exec_id, "executive-brief-rollup", depends_on=[rollup_id])

    _run(db, exec_id, "executive-brief-rollup")

    expected = db.get_result_ref(rollup_id)["executive_body"]
    assert list(clients._briefs.values())[-1]["body"] == expected
    assert (
        db.get_result_ref(exec_id)["morning_brief_id"]
        == db.get_result_ref(rollup_id)["brief_id"]
    )


# --- publish ---------------------------------------------------------------


def test_the_brief_is_announced_only_after_qa(clients, monkeypatch):
    """F-BRIEF-ANNOUNCED-BEFORE-QA. draft_brief_handler announced the
    brief the moment it created it, before qa had run -- so a brief that
    then failed QA had already been sent, and the block that followed was
    invisible to whoever read it."""
    from orchestrator import teams_notify

    announced: list[dict] = []
    monkeypatch.setattr(
        teams_notify, "notify_brief_ready", lambda **kw: announced.append(kw) or True
    )
    db = FakeTaskDB()
    ingest_id, draft_id = str(uuid.uuid4()), str(uuid.uuid4())
    db.seed(ingest_id, "ingest-signals")
    db.seed(draft_id, "draft-brief", depends_on=[ingest_id])
    dispatch.ingest_signals_handler(ingest_id, _envelope(ingest_id, "ingest-signals"), db)
    dispatch.draft_brief_handler(draft_id, _envelope(draft_id, "draft-brief"), db)

    assert announced == [], "the brief was announced before its QA gate ran"

    qa_id, publish_id = str(uuid.uuid4()), str(uuid.uuid4())
    db.seed(qa_id, "qa-review", depends_on=[draft_id])
    db.seed(publish_id, "publish-brief", depends_on=[qa_id])
    dispatch.qa_review_handler(qa_id, _envelope(qa_id, "qa-review"), db)

    _run(db, publish_id, "publish-brief")

    assert len(announced) == 1
    assert announced[0]["brief_id"] == db.get_result_ref(draft_id)["brief_id"]
    assert db.get_result_ref(publish_id)["notified"] is True
