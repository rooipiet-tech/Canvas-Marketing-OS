"""Tests for function 19, the competitor proposer (F-COMPETITOR-REGISTER).

functions/_shared/competitor-register.yaml was a FIXED list. Function 10
has carried a `new-entrant` taxonomy from the start and can genuinely card
an unfamiliar supplier it finds in trade press or a tender award -- but
nothing read that card back, so a new competitor reached the morning brief
and stopped there.

Three properties matter more than the proposing itself, and all three are
here:

  * Function 19 is permitted NOTHING. Giving a competitor proposer
    retrieval would let its own suspicion that some firm competes with
    Canvas cause a request to that firm, and let the response justify the
    suspicion.
  * It cannot edit the register. Adding an entry grants a name standing
    across twelve prompts, so the handler ends at a gate-check under its
    own autonomy entry and a person makes the edit.
  * A quiet week costs nothing and raises nothing. An approval that is
    usually empty is one a reviewer learns to close unread.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
import yaml
from orchestrator import dispatch
from orchestrator.config import functions_dir
from tests.fakes import patch_dispatch_clients
from tests.test_dispatch import FakeTaskDB, _envelope

REPO_ROOT = Path(__file__).resolve().parents[3]
LOOP_PATH = REPO_ROOT / "services/orchestrator/loops/source-discovery-loop.yaml"
AUTONOMY_PATH = REPO_ROOT / "services/gatekeeper/policy/autonomy.yaml"

NEW_ENTRANT_CARD = {
    "headline": "Northfield Analytics opens a Johannesburg consolidation practice",
    "so_what": "An unfamiliar supplier entering the market Canvas sells into",
    "source_url": "https://www.itweb.co.za/article/northfield-jhb",
    "taxonomy": "new-entrant",
    "evidence_grade": "moderate",
    "confidence": "medium",
}


def _seed_cards(vault, cards: list[dict[str, Any]], *, topic: str = "A scan topic") -> None:
    """Write a scanner card batch the way _make_scanner_handler does."""
    vault.create_signal(
        source="function-10-competitor-discovery-scanner",
        signal_type=dispatch.CARD_BATCH_TYPE,
        payload={"topic": topic, "horizon_days": 30, "summary": "x", "cards": cards},
        campaign_id="c",
        function_id="10-competitor-discovery-scanner",
    )


class _ScoutGateway:
    """Returns whatever candidates the test asks for, echoing the horizon."""

    def __init__(self, candidates: list[dict[str, Any]]) -> None:
        self.candidates = candidates
        self.calls: list[dict[str, Any]] = []

    def __enter__(self) -> "_ScoutGateway":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        pass

    def complete(self, *, agent_run_id: str, user_content: str, model: str = "x", **kw: Any):
        self.calls.append({"user_content": user_content, **kw})
        horizon = json.loads(user_content)["horizon_days"]
        return {
            "id": f"fake-{uuid.uuid4()}",
            "model": model,
            "content": json.dumps({"horizon_days": horizon, "candidates": self.candidates}),
            "usage": {"input_tokens": 10, "output_tokens": 10},
            "agent_run_id": agent_run_id,
        }


class _RecordingGatekeeper:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __enter__(self) -> "_RecordingGatekeeper":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        pass

    def gate_check(self, **kw: Any) -> dict[str, Any]:
        self.calls.append(kw)
        return {"decision_id": "d", "outcome": "queued", "approval_id": "a"}


@pytest.fixture()
def clients(monkeypatch):
    return patch_dispatch_clients(monkeypatch)


def _candidate(**overrides: Any) -> dict[str, Any]:
    candidate = {
        "name": "Northfield Analytics",
        "kind": "firm",
        "evidence_headline": NEW_ENTRANT_CARD["headline"],
        "source_url": NEW_ENTRANT_CARD["source_url"],
        "rationale": "Sells multi-entity consolidation into the groups Canvas sells to",
        "confidence": "low",
    }
    candidate.update(overrides)
    return candidate


def _wire(monkeypatch, candidates: list[dict[str, Any]]):
    gateway, gatekeeper = _ScoutGateway(candidates), _RecordingGatekeeper()
    monkeypatch.setattr(dispatch, "build_gateway_client", lambda: gateway)
    monkeypatch.setattr(dispatch, "build_gatekeeper_client", lambda: gatekeeper)
    return gateway, gatekeeper


def _run(db: FakeTaskDB) -> str:
    task_id = str(uuid.uuid4())
    db.seed(task_id, "propose-competitors")
    dispatch.propose_competitors_handler(task_id, _envelope(task_id, "propose-competitors"), db)
    return task_id


# ---------------------------------------------------------------------
# The package
# ---------------------------------------------------------------------


def test_function_19_declares_itself_permitted_nothing():
    manifest = yaml.safe_load(
        (functions_dir() / "19-competitor-scout" / "tools.yaml").read_text(encoding="utf-8")
    )

    assert [tool["permissions"] for tool in manifest["tools"]] == ["none"]


def test_the_handler_gives_function_19_no_retrieval_either():
    """Nothing in the proposing path may fetch: it reads the Vault and the
    gateway, never mcp-web."""
    import inspect

    source = inspect.getsource(dispatch.propose_competitors_handler)

    assert "build_mcp_web_client" not in source
    assert "fetch_url" not in source
    assert "probe_url" not in source


def test_the_task_is_registered_and_the_loop_declares_it():
    assert dispatch.DISPATCH_TABLE["propose-competitors"] is dispatch.propose_competitors_handler

    loop = yaml.safe_load(LOOP_PATH.read_text(encoding="utf-8"))
    task = next(t for t in loop["tasks"] if t["task_type"] == "propose-competitors")
    assert task["depends_on"] == [], "it must be a root task, independent of source proposal"


def test_adding_a_competitor_has_its_own_autonomy_entry():
    """Adding an organisation to the register is not the same decision as
    allow-listing a host, and the approval surface should not have to guess
    which one it is looking at."""
    entries = yaml.safe_load(AUTONOMY_PATH.read_text(encoding="utf-8"))["entries"]
    entry = next(e for e in entries if e["function_id"] == "config.competitor_register")

    assert entry["action_class"] == "configure"
    assert entry["level"] == 1, "a register addition always takes a human approver"
    assert dispatch.FUNCTION_ID_COMPETITOR_REGISTER == "config.competitor_register"


# ---------------------------------------------------------------------
# A quiet week
# ---------------------------------------------------------------------


def test_a_week_with_no_new_entrant_cards_spends_nothing_and_raises_nothing(clients, monkeypatch):
    """Most weeks. No model call, no card: an approval that is usually
    empty is one a reviewer learns to close unread."""
    _seed_cards(clients, [{**NEW_ENTRANT_CARD, "taxonomy": "pricing-move"}])

    def _explode():
        raise AssertionError("a quiet week must not reach the gateway")

    monkeypatch.setattr(dispatch, "build_gateway_client", _explode)
    _gateway, gatekeeper = None, _RecordingGatekeeper()
    monkeypatch.setattr(dispatch, "build_gatekeeper_client", lambda: gatekeeper)

    db = FakeTaskDB()
    task_id = _run(db)

    ref = db.get_result_ref(task_id)
    assert db.get_task(task_id)["state"] == "completed"
    assert ref["status"] == "nothing_to_propose"
    assert ref["card_count"] == 0
    assert gatekeeper.calls == []


def test_a_run_that_proposes_nothing_raises_no_card_either(clients, monkeypatch):
    """The model read the cards and found no new competitor. That is a real
    answer and does not need a person."""
    _seed_cards(clients, [NEW_ENTRANT_CARD])
    _gateway, gatekeeper = _wire(monkeypatch, [])

    db = FakeTaskDB()
    task_id = _run(db)

    ref = db.get_result_ref(task_id)
    assert ref["status"] == "nothing_to_propose"
    assert ref["card_count"] == 1
    assert ref["proposal_batch_id"], "the empty result is still recorded in the Vault"
    assert gatekeeper.calls == []


# ---------------------------------------------------------------------
# Proposing
# ---------------------------------------------------------------------


def test_a_new_entrant_card_becomes_one_approval_card(clients, monkeypatch):
    _seed_cards(clients, [NEW_ENTRANT_CARD])
    gateway, gatekeeper = _wire(monkeypatch, [_candidate()])

    db = FakeTaskDB()
    task_id = _run(db)

    ref = db.get_result_ref(task_id)
    assert ref["status"] == "proposed"
    assert ref["proposed_names"] == ["Northfield Analytics"]
    assert ref["card_count"] == 1
    assert len(gatekeeper.calls) == 1

    call = gatekeeper.calls[0]
    assert call["function_id"] == "config.competitor_register"
    assert call["action_class"] == "configure"
    assert call["preview_title"].startswith("Competitor register")

    payload = json.loads(gateway.calls[0]["user_content"])
    assert payload["cards"][0]["headline"] == NEW_ENTRANT_CARD["headline"]
    assert {c["name"] for c in payload["known_competitors"]} >= {"DVT", "PBT Group"}


def test_only_new_entrant_cards_reach_the_scout(clients, monkeypatch):
    """A pricing move by a known competitor is the scanners' subject, not
    this function's."""
    _seed_cards(
        clients,
        [
            NEW_ENTRANT_CARD,
            {
                **NEW_ENTRANT_CARD,
                "headline": "A known competitor raises prices",
                "taxonomy": "pricing-move",
            },
        ],
    )
    gateway, _gatekeeper = _wire(monkeypatch, [_candidate()])

    _run(FakeTaskDB())

    payload = json.loads(gateway.calls[0]["user_content"])
    assert [c["headline"] for c in payload["cards"]] == [NEW_ENTRANT_CARD["headline"]]


def test_the_card_carries_the_evidence_a_reviewer_needs_to_check_it():
    evidence = dispatch._render_competitor_evidence([_candidate()], [], 1)

    assert "Northfield Analytics" in evidence
    assert NEW_ENTRANT_CARD["headline"] in evidence
    assert NEW_ENTRANT_CARD["source_url"] in evidence
    assert "twelve prompts" in evidence
    assert "does NOT add any source url" in evidence.replace("\n", " ")


# ---------------------------------------------------------------------
# The two deterministic filters — prompt rules 1 and 3, enforced in code
# ---------------------------------------------------------------------


def test_a_proposal_citing_no_supplied_card_is_dropped(clients, monkeypatch):
    """The anti-invention filter. A name the model recalled rather than
    read is exactly how an organisation that was never in the evidence
    ends up on an approval card looking sourced."""
    _seed_cards(clients, [NEW_ENTRANT_CARD])
    _gateway, gatekeeper = _wire(
        monkeypatch,
        [
            _candidate(),
            _candidate(
                name="Recalled From Training",
                evidence_headline="A headline no supplied card carries",
                source_url="https://example.invalid/not-in-any-card",
            ),
        ],
    )

    db = FakeTaskDB()
    task_id = _run(db)

    ref = db.get_result_ref(task_id)
    assert ref["proposed_names"] == ["Northfield Analytics"]
    assert ref["dropped_count"] == 1
    assert "Recalled From Training" in gatekeeper.calls[0]["evidence_summary"]
    assert "cites evidence no card in this window carries" in gatekeeper.calls[0][
        "evidence_summary"
    ]


def test_a_proposal_already_in_the_register_is_dropped_suffix_insensitively(clients, monkeypatch):
    """"Cobalt Analytics Ltd" must not enter as a second Cobalt Analytics."""
    card = {**NEW_ENTRANT_CARD, "headline": "Cobalt Analytics Ltd wins a provincial tender"}
    _seed_cards(clients, [card])
    _gateway, gatekeeper = _wire(
        monkeypatch,
        [
            _candidate(
                name="Cobalt Analytics Ltd",
                evidence_headline=card["headline"],
                source_url=card["source_url"],
            )
        ],
    )

    db = FakeTaskDB()
    task_id = _run(db)

    ref = db.get_result_ref(task_id)
    assert ref["status"] == "nothing_to_propose"
    assert ref["dropped_count"] == 1
    assert gatekeeper.calls == [], "nothing survived, so nothing is put to a person"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Cobalt Analytics Ltd", "cobalt analytics"),
        ("Cobalt Analytics (Pty) Ltd", "cobalt analytics"),
        ("Strategix Group", "strategix"),
        ("  DVT  ", "dvt"),
    ],
)
def test_name_normalisation_matches_the_prompts_own_comparison(raw, expected):
    assert dispatch._normalise_competitor_name(raw) == expected


# ---------------------------------------------------------------------
# The security property
# ---------------------------------------------------------------------


def test_the_handler_never_edits_the_register(clients, monkeypatch):
    """A run must leave the register byte-identical: adding a competitor is
    a person's edit after approving the card."""
    register_path = functions_dir() / "_shared" / "competitor-register.yaml"
    before = register_path.read_bytes()

    _seed_cards(clients, [NEW_ENTRANT_CARD])
    _wire(monkeypatch, [_candidate()])
    _run(FakeTaskDB())

    assert register_path.read_bytes() == before
