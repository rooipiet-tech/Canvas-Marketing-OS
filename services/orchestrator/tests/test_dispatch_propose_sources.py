"""Tests for function 17, the source proposer (F-SOURCE-DISCOVERY, part 2).

The probe pipeline could test candidate sources but only a human could
think of them, so most profiles stayed empty. Function 17 proposes
addresses from the profile's own topic and watchlist prose -- plus, for
the profiles that watch a named competitor set rather than a sector, the
competitor register.

Two properties matter more than the proposing itself, and both are here:

  * Function 17 is permitted NOTHING. Giving a proposer retrieval would
    let a model's own suggestion cause an outbound request to an arbitrary
    host -- the circularity the sandboxed probe exists to break.
  * A proposal cannot reach the probe in the same run. probe_url reads
    MCP_WEB_PROBE_ALLOWLIST, so a host nobody has cleared is unprobeable,
    and this handler ends at a card asking a human to clear it. The
    smaller gate is the more important one: it stops a model's guess from
    causing a network call at all.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
import yaml
from orchestrator import dispatch
from orchestrator.config import functions_dir
from tests.fakes import patch_dispatch_clients
from tests.test_dispatch import FakeTaskDB, _envelope


def _candidates(n: int = 3) -> list[dict[str, Any]]:
    pool = [
        ("https://www.itweb.co.za/rss/news", "ITWeb", "rss", "high"),
        ("https://www.engineeringnews.co.za/rss", "Engineering News", "rss", "medium"),
        ("https://www.example-trade.co.za/feed/", "A trade title", "rss", "low"),
    ]
    return [
        {
            "url": url,
            "publisher": publisher,
            "source_kind": kind,
            "rationale": "Covers the sector this profile's watchlist names, in its own market",
            "confidence": confidence,
        }
        for url, publisher, kind, confidence in pool[:n]
    ]


class _ScoutGateway:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __enter__(self) -> "_ScoutGateway":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        pass

    def complete(self, *, agent_run_id: str, user_content: str, model: str = "x", **kw: Any):
        self.calls.append({"user_content": user_content, **kw})
        profile_id = json.loads(user_content)["profile_id"]
        return {
            "id": f"fake-{uuid.uuid4()}",
            "model": model,
            "content": json.dumps({"profile_id": profile_id, "candidates": _candidates()}),
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


@pytest.fixture()
def wired(monkeypatch, clients):
    gateway, gatekeeper = _ScoutGateway(), _RecordingGatekeeper()
    monkeypatch.setattr(dispatch, "build_gateway_client", lambda: gateway)
    monkeypatch.setattr(dispatch, "build_gatekeeper_client", lambda: gatekeeper)
    return {"gateway": gateway, "gatekeeper": gatekeeper, "clients": clients}


def _run(db: FakeTaskDB) -> str:
    task_id = str(uuid.uuid4())
    db.seed(task_id, "propose-sources")
    dispatch.propose_sources_handler(task_id, _envelope(task_id, "propose-sources"), db)
    return task_id


# ---------------------------------------------------------------------
# The package
# ---------------------------------------------------------------------


def test_function_17_declares_itself_permitted_nothing():
    """The restriction is the design, so it must be visible in the manifest
    rather than merely true of the handler."""
    manifest = yaml.safe_load(
        (functions_dir() / "17-source-scout" / "tools.yaml").read_text(encoding="utf-8")
    )

    assert [tool["permissions"] for tool in manifest["tools"]] == ["none"]


def test_the_handler_gives_function_17_no_tools_either():
    """Nothing in the proposing path may fetch: the handler calls the
    gateway and the Vault, never mcp-web."""
    import inspect

    source = inspect.getsource(dispatch.propose_sources_handler)

    assert "build_mcp_web_client" not in source
    assert "fetch_url" not in source
    assert "probe_url" not in source


# ---------------------------------------------------------------------
# The competitor register — what unblocked profiles 11, 12 and 13
# ---------------------------------------------------------------------
#
# Those three are per-COMPETITOR scans whose watchlists ask for
# competitor-owned channels, and the scout proposes from `topic` and
# `watchlist_note` alone -- neither of which names a competitor. So it
# had nothing to turn into a newsroom address, and the two YAML files
# recorded the gap as waiting on "the competitor register being
# consolidated out of the eleven prompts". These tests hold that register
# to the shape the pipeline depends on.


def test_the_register_holds_the_same_names_the_prompts_do():
    """The register was consolidated out of prose repeated verbatim in
    twelve prompt.md files. If someone adds a competitor to the prompts
    and not here, the scout proposes for a set the scanners do not watch
    -- so the two are asserted against each other rather than trusted."""
    register = {entry["name"] for entry in dispatch._load_competitor_register()}
    # Collapsed, because the naming rule is hard-wrapped prose: "Altron
    # Digital Business" straddles a line break in every copy of it.
    prompt = " ".join(
        (functions_dir() / "25-competitive-response-strategist" / "prompt.md")
        .read_text(encoding="utf-8")
        .split()
    )

    assert register, "the register must not be empty"
    for name in register:
        assert name in prompt, f"{name!r} is in the register but named in no prompt"


def test_the_register_carries_no_urls():
    """The security line this whole pipeline is built on. A newsroom
    address written into config would be an unprobed host reaching a scan
    profile without the probe evidence and the card -- the exact bypass
    source-candidates.yaml exists to prevent."""
    raw = (functions_dir() / "_shared" / "competitor-register.yaml").read_text(encoding="utf-8")
    body = "\n".join(line for line in raw.splitlines() if not line.lstrip().startswith("#"))

    assert "http://" not in body
    assert "https://" not in body


def test_only_profiles_that_ask_for_it_are_given_competitors(wired):
    """A vertical scan wants sector press and tender portals. Handing it a
    competitor list would pull its proposals toward vendor newsrooms and
    away from the sector it is meant to listen to."""
    _run(FakeTaskDB())

    payloads = [json.loads(call["user_content"]) for call in wired["gateway"].calls]
    assert payloads, "the scout was never called"

    for payload in payloads:
        profile = dispatch._resolve_scan_profile(payload["profile_id"], require_urls=False)
        if profile.get("needs_competitor_register"):
            assert payload["competitors"], f"{payload['profile_id']} asked for the register"
        else:
            assert "competitors" not in payload

    given = [p["profile_id"] for p in payloads if p.get("competitors")]
    assert set(given) == {
        "competitor-change",
        "competitive-positioning",
        "competitor-content-performance",
    }


def test_the_competitors_payload_validates_against_function_17s_schema(wired):
    """The input contract is `additionalProperties: false`, so a new field
    that the schema does not know about fails validation rather than being
    quietly dropped. _validate_function_input runs inside the handler --
    this asserts the schema was updated alongside it."""
    _run(FakeTaskDB())

    payload = next(
        json.loads(call["user_content"])
        for call in wired["gateway"].calls
        if "competitors" in json.loads(call["user_content"])
    )

    dispatch._validate_function_input("17-source-scout", payload)
    assert {entry["kind"] for entry in payload["competitors"]} <= {"firm", "product", "category"}


def test_a_category_entry_is_marked_so_the_scout_does_not_invent_a_newsroom():
    """"the Big Four SA data practices" names no single organisation. The
    scout is told that rather than left to guess, which is what prompt.md
    hard rule 10 acts on."""
    register = dispatch._load_competitor_register()
    kinds = {entry["name"]: entry["kind"] for entry in register}

    assert kinds["the Big Four SA data practices"] == "category"
    assert kinds["RIB BI+"] == "product"
    assert kinds["DVT"] == "firm"


# ---------------------------------------------------------------------
# Proposing
# ---------------------------------------------------------------------


def _sourceless_profile_ids() -> list[str]:
    """Profiles with no `urls`, read from the shipped scan-profiles.yaml.

    This used to be the literal 11 -- true while market-intelligence was
    the only sourced profile, and false from the moment
    competitor-discovery and fabric-ecosystem were promoted on
    2 Sep 2026. The handler's rule is "propose for profiles that lack
    sources", so the test should assert that rule, not a snapshot of how
    many profiles happened to lack them.
    """
    document = yaml.safe_load(
        (functions_dir() / "_shared" / "scan-profiles.yaml").read_text(encoding="utf-8")
    )
    return [p["profile_id"] for p in document["profiles"] if not p.get("urls")]


def test_only_profiles_that_lack_sources_are_proposed_for(wired):
    """A profile with urls must not be re-proposed for."""
    _run(FakeTaskDB())

    asked = [json.loads(call["user_content"])["profile_id"] for call in wired["gateway"].calls]
    expected = _sourceless_profile_ids()

    assert expected, "every profile has sources -- this handler has nothing left to do"
    assert sorted(asked) == sorted(expected)
    assert len(set(asked)) == len(asked), "a profile was proposed for twice"
    # The promoted ones specifically: proposing for a profile that already
    # scans wastes a probe and a reviewer's attention.
    assert "market-intelligence" not in asked
    assert "competitor-discovery" not in asked
    assert "fabric-ecosystem" not in asked


def test_the_scout_is_told_what_the_register_already_holds(wired, monkeypatch):
    """A source already awaiting a probe must not be proposed again -- it
    wastes a probe and a reviewer's attention.

    This used to read competitor-discovery's three shipped candidates
    straight out of source-candidates.yaml. Promoting that profile made
    the assertion a KeyError: a sourced profile is not proposed for at
    all, so it has no payload to inspect.

    The register is now seeded here instead of borrowed from the shipped
    file. That is the more honest test either way -- it stopped depending
    on which profiles happen to have candidates today, and the invariant
    it checks (whatever the register holds for a profile is handed to the
    scout for that profile, and only that profile) is stated directly.
    """
    target, other = _sourceless_profile_ids()[:2]
    seeded = [
        {"profile_id": target, "url": "https://seeded-one.example/feed"},
        {"profile_id": target, "url": "https://seeded-two.example/feed"},
        {"profile_id": other, "url": "https://seeded-three.example/feed"},
    ]
    monkeypatch.setattr(dispatch, "_load_source_candidates", lambda: seeded)

    _run(FakeTaskDB())

    by_profile = {
        json.loads(call["user_content"])["profile_id"]: json.loads(call["user_content"])
        for call in wired["gateway"].calls
    }

    assert by_profile[target]["existing_candidates"] == [
        "https://seeded-one.example/feed",
        "https://seeded-two.example/feed",
    ]
    # Scoped per profile, not a global list: telling every scout about
    # every candidate would suppress a genuinely new proposal elsewhere.
    assert by_profile[other]["existing_candidates"] == ["https://seeded-three.example/feed"]


def test_every_payload_satisfies_function_17s_own_schema(wired):
    _run(FakeTaskDB())

    for call in wired["gateway"].calls:
        dispatch._validate_function_input(
            dispatch.FUNCTION_ID_17, json.loads(call["user_content"])
        )  # must not raise


def test_proposals_are_recorded_as_their_own_batch_type(wired):
    """A proposal is not a market signal and must never be recalled as one
    by the scans' cross-run memory."""
    db = FakeTaskDB()
    task_id = _run(db)

    assert dispatch.PROPOSAL_BATCH_TYPE not in dispatch.SCAN_BATCH_TYPES
    batch = wired["clients"]._signals[db.get_result_ref(task_id)["proposal_batch_id"]]
    assert batch["signal_type"] == dispatch.PROPOSAL_BATCH_TYPE


# ---------------------------------------------------------------------
# The card — the gate that stops a guess causing a network call
# ---------------------------------------------------------------------


def test_the_card_asks_only_for_probe_access_never_scan_access(wired):
    _run(FakeTaskDB())
    evidence = wired["gatekeeper"].calls[0]["evidence_summary"]

    assert "PROBE" in evidence
    assert "does NOT put them on the scan allow-list" in evidence
    assert "second card" in evidence


def test_the_card_says_nothing_was_fetched(wired):
    """The reviewer must know these are addresses a model believes exist,
    not addresses anyone has tested."""
    evidence = (_run(FakeTaskDB()), wired["gatekeeper"].calls[0]["evidence_summary"])[1]

    assert "Nothing has been fetched" in evidence
    assert "no retrieval tools" in evidence


def test_the_card_carries_each_proposal_with_its_address_confidence(wired):
    """Confidence here is "does this URL exist", which is the honest measure
    of how much of the list is likely to be wrong."""
    evidence = (_run(FakeTaskDB()), wired["gatekeeper"].calls[0]["evidence_summary"])[1]

    assert "www.itweb.co.za" in evidence
    assert "address confidence: high" in evidence
    assert "address confidence: low" in evidence


def test_the_gate_check_uses_the_configure_action(wired):
    _run(FakeTaskDB())
    call = wired["gatekeeper"].calls[0]

    assert call["function_id"] == dispatch.FUNCTION_ID_SOURCE_PROMOTION
    assert call["action_class"] == dispatch.SOURCE_PROMOTION_ACTION_CLASS
    assert call["preview_title"].startswith("Probe allow-list")


def test_proposing_never_edits_a_profile_or_either_allowlist(wired):
    """The security property, same as the probe step's: a run leaves both
    files byte-identical."""
    profiles = functions_dir() / "_shared" / "scan-profiles.yaml"
    candidates = functions_dir() / "_shared" / "source-candidates.yaml"
    bicep = functions_dir().parent / "infra" / "main.bicep"
    before = (profiles.read_bytes(), candidates.read_bytes(), bicep.read_bytes())

    _run(FakeTaskDB())

    assert (profiles.read_bytes(), candidates.read_bytes(), bicep.read_bytes()) == before


def test_the_run_records_the_hosts_a_human_is_being_asked_to_clear(wired):
    db = FakeTaskDB()
    task_id = _run(db)
    ref = db.get_result_ref(task_id)

    assert ref["status"] == "proposed"
    # Derived, not the literal 11 -- see _sourceless_profile_ids.
    assert ref["profile_count"] == len(_sourceless_profile_ids())
    assert "www.itweb.co.za" in ref["proposed_hosts"]
    assert ref["decision_id"]


def test_nothing_to_propose_completes_without_a_card(wired, monkeypatch):
    """Once every profile is sourced, this step should go quiet rather than
    ask for something."""
    monkeypatch.setattr(dispatch, "_profiles_needing_sources", lambda: [])

    db = FakeTaskDB()
    task_id = _run(db)

    assert db.get_result_ref(task_id)["status"] == "nothing_to_propose"
    assert wired["gatekeeper"].calls == []
    assert wired["gateway"].calls == []
