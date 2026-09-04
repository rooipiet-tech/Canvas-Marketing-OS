"""Tests for the eleven S10 fan-out scanners (F1).

The architecture review's highest-value finding: eleven complete function
packages -- prompt, schema, tools, skill, 5 evals each -- each with a task
in daily-signal-loop.yaml and no DISPATCH_TABLE entry, so all eleven fell
through to legacy_task_pass_through. The loop reported 23 completed tasks
every morning while about 74% of its declared work produced nothing.

All eleven profiles are still sourceless (nobody has written down where to
read each sector yet), so the behaviour that matters most here is what a
wired-but-unsourced scanner does: it COMPLETES as not_configured, without
a model call, rather than failing. Eleven daily failures would cascade
into dedupe and both rollups and make red the normal state, which is how
a red loop stops meaning anything. The difference from the no-op it
replaces is that the emptiness is now named and queryable.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
import yaml
from orchestrator import dispatch
from orchestrator.config import functions_dir
from tests.fakes import _SCANNER_BATCHES, patch_dispatch_clients, patch_scan_profiles
from tests.test_dispatch import FakeTaskDB, _envelope

LOOP_PATH = (
    functions_dir().parent / "services" / "orchestrator" / "loops" / "daily-signal-loop.yaml"
)
FABRIC_URL = "https://learn.microsoft.com/en-us/fabric/get-started/whats-new"
MONEYWEB_URL = "https://www.moneyweb.co.za/feed/"


@pytest.fixture()
def clients(monkeypatch):
    return patch_dispatch_clients(monkeypatch)


# ---------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------


def test_all_eleven_scanners_have_a_real_handler():
    """The finding itself: no scanner may fall through to pass-through."""
    assert len(dispatch.SCANNER_TASKS) == 11
    for task_type in dispatch.SCANNER_TASKS:
        assert dispatch.DISPATCH_TABLE.get(task_type) is not None, task_type


def test_every_task_running_off_the_ingest_is_registered():
    """Catches the original bug directly -- a loop task whose task_type has
    no DISPATCH_TABLE entry. Everything hanging off `ingest` (the eleven
    scanners plus score-signals) must now resolve to a real handler."""
    loop = yaml.safe_load(LOOP_PATH.read_text(encoding="utf-8"))
    off_ingest = [task for task in loop["tasks"] if task["depends_on"] == ["ingest"]]

    assert len(off_ingest) == 12  # 11 scanners + score-signals
    for task in off_ingest:
        assert task["task_type"] in dispatch.DISPATCH_TABLE, task["task_type"]


def test_every_registered_scanner_actually_appears_in_the_loop():
    """The other direction: a handler nothing dispatches is dead code."""
    loop = yaml.safe_load(LOOP_PATH.read_text(encoding="utf-8"))
    loop_task_types = {task["task_type"] for task in loop["tasks"]}

    assert set(dispatch.SCANNER_TASKS) <= loop_task_types


def test_each_scanner_points_at_a_package_and_a_profile_that_exist():
    profiles = yaml.safe_load(
        (functions_dir() / "_shared" / "scan-profiles.yaml").read_text(encoding="utf-8")
    )
    known = {profile["profile_id"] for profile in profiles["profiles"]}

    for task_type, (function_id, profile_id, _agent) in dispatch.SCANNER_TASKS.items():
        assert (functions_dir() / function_id).is_dir(), task_type
        assert profile_id in known, task_type


def test_the_loop_and_the_handler_table_agree_on_every_profile():
    """Two places name a profile per task: the loop's params and the
    handler's default. Disagreement would mean the loop silently scanned
    something other than what the table says."""
    loop = yaml.safe_load(LOOP_PATH.read_text(encoding="utf-8"))
    by_task_type = {task["task_type"]: task for task in loop["tasks"]}

    for task_type, (_function_id, profile_id, _agent) in dispatch.SCANNER_TASKS.items():
        assert by_task_type[task_type]["params"]["profile_id"] == profile_id


def test_handlers_are_distinct_objects_with_readable_names():
    """One factory, eleven registrations -- but a traceback must still say
    which scanner failed."""
    handlers = [dispatch.DISPATCH_TABLE[task_type] for task_type in dispatch.SCANNER_TASKS]

    assert len({id(handler) for handler in handlers}) == 11
    assert dispatch.DISPATCH_TABLE["fabric-ecosystem-scout"].__name__ == (
        "fabric_ecosystem_scout_handler"
    )


# ---------------------------------------------------------------------
# The unsourced path — the behaviour of every scanner still awaiting
# sources
# ---------------------------------------------------------------------


# One scanner task stood in for "a scanner whose profile has no urls".
# This used to be derived from scan-profiles.yaml, which was right while
# some profile was always empty and wrong the moment PR 5a filled every
# one in: the derivation returned [], the parametrize below collected zero
# cases, and the not_configured path stopped being tested without any test
# going red. The path itself is still live -- a profile is emptied when its
# last source is retired -- so the tests construct that state now instead
# of finding it.
UNSOURCED_TASK_TYPE = "vertical-scan-mining-industrial"
UNSOURCED_PROFILE_ID = dispatch.SCANNER_TASKS[UNSOURCED_TASK_TYPE][1]


def test_every_scanner_has_exactly_one_fake_gateway_batch():
    """The fake must route every scanner to its own batch, by the same
    rule the fake itself uses: the first _SCANNER_BATCHES title that is a
    substring of the system prompt.

    This trap has sprung three times. Each time, a scanner that was
    previously never reaching the gateway started to, matched no batch,
    fell through to a later branch that does json.loads(user_content), and
    died on a bare JSONDecodeError naming nothing. It cost two debugging
    passes before PR 5a and took out nine scanners at once during it, when
    the bootstrap sourced every remaining profile.

    Asserting the mapping is complete AND unambiguous is what stops a
    fourth: a missing title fails here, and so does a title that matches
    two scanners' prompts (which would silently seed the wrong batch).
    """
    for task_type, (function_id, _profile_id, _agent) in sorted(dispatch.SCANNER_TASKS.items()):
        prompt = (functions_dir() / function_id / "prompt.md").read_text(encoding="utf-8")
        matches = [title for title in _SCANNER_BATCHES if title in prompt]

        assert matches, (
            f"{task_type} ({function_id}) matches no _SCANNER_BATCHES title -- it will "
            "fall through to a later branch of the fake gateway and fail obscurely"
        )
        assert len(matches) == 1, (
            f"{task_type} ({function_id}) matches {matches}; the fake takes the first, "
            "so one of these scanners would be seeded with another's batch"
        )


def test_every_scanner_profile_is_sourced():
    """The guard, inverted to match reality after PR 5a.

    It used to assert that SOME scanner was still awaiting sources, to stop
    the unsourced tests going vacuous. That is no longer true and must not
    be: a scanner whose profile is empty completes as not_configured every
    morning and produces nothing.
    """
    unsourced = sorted(
        task_type
        for task_type, (_function_id, profile_id, _agent_name) in dispatch.SCANNER_TASKS.items()
        if not dispatch._resolve_scan_profile(profile_id, require_urls=False).get("urls")
    )

    assert not unsourced, (
        f"{unsourced} have no urls -- they will complete as not_configured, "
        "not scan; fill the profile in or retire the scanner"
    )


def test_an_unsourced_scanner_completes_as_not_configured(clients, caplog, monkeypatch):
    task_type = UNSOURCED_TASK_TYPE
    patch_scan_profiles(monkeypatch, sourceless=(UNSOURCED_PROFILE_ID,))
    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    db.seed(task_id, task_type)

    with caplog.at_level("WARNING"):
        dispatch.DISPATCH_TABLE[task_type](task_id, _envelope(task_id, task_type), db)

    assert db.get_task(task_id)["state"] == "completed"
    ref = db.get_result_ref(task_id)
    assert ref["status"] == "not_configured"
    assert ref["profile_id"] == dispatch.SCANNER_TASKS[task_type][1]
    assert "scan-profiles.yaml" in ref["reason"]
    assert "scan_profile_not_configured" in caplog.text


def test_an_unsourced_scanner_spends_nothing(clients, monkeypatch):
    """No model call, and no Vault agent_run either -- an unconfigured
    scanner should cost exactly zero, not a cheap call on empty evidence."""

    def _explode():
        raise AssertionError("an unsourced scanner must not reach the gateway")

    monkeypatch.setattr(dispatch, "build_gateway_client", _explode)
    monkeypatch.setattr(dispatch, "build_mcp_web_client", _explode)
    patch_scan_profiles(monkeypatch, sourceless=(UNSOURCED_PROFILE_ID,))

    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    db.seed(task_id, "vertical-scan-mining-industrial")
    dispatch.DISPATCH_TABLE["vertical-scan-mining-industrial"](
        task_id, _envelope(task_id, "vertical-scan-mining-industrial"), db
    )

    assert db.get_result_ref(task_id)["status"] == "not_configured"
    assert not clients._agent_runs


def test_not_configured_is_distinguishable_from_a_real_scan(clients, monkeypatch):
    """The whole point of replacing the no-op: /status can tell an
    unconfigured scanner from one that ran."""
    patch_scan_profiles(
        monkeypatch, sourceless=(dispatch.SCANNER_TASKS["vertical-scan-construction"][1],)
    )
    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    db.seed(task_id, "vertical-scan-construction")
    dispatch.DISPATCH_TABLE["vertical-scan-construction"](
        task_id, _envelope(task_id, "vertical-scan-construction"), db
    )

    ref = db.get_result_ref(task_id)
    assert ref["status"] == "not_configured"
    assert "vault_signal_id" not in ref


# ---------------------------------------------------------------------
# The sourced path — what happens the moment a profile is filled in
# ---------------------------------------------------------------------


class _CardsGatewayClient:
    """Returns a schema-valid card batch for whichever scanner asked."""

    def __init__(self, cards: list[dict[str, Any]] | None = None) -> None:
        self._cards = cards
        self.calls: list[dict[str, Any]] = []

    def __enter__(self) -> "_CardsGatewayClient":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        pass

    def complete(self, *, agent_run_id: str, model: str = "claude-haiku", **kw: Any) -> dict:
        self.calls.append(kw)
        cards = self._cards or [
            {
                "headline": "A construction group consolidated its plant ledgers",
                "so_what": "Shows the consolidation pillar landing with this buyer",
                "source_url": FABRIC_URL,
                "card_type": "opportunity",
                "taxonomy": "cfo-pain-signal",
                "evidence_grade": "moderate",
                "confidence": "high",
            },
            {
                "headline": "A construction-sector ERP tender scored group reporting",
                "so_what": "The buyer is procuring against the consolidation pillar",
                "source_url": MONEYWEB_URL,
                "card_type": "opportunity",
                "taxonomy": "tender-signal",
                "evidence_grade": "moderate",
                "confidence": "medium",
            },
            {
                "headline": "A BI vendor targeted construction finance reporting",
                "so_what": "One competitor strand of this vertical, worth tracking",
                "source_url": "https://businesstech.co.za/news/feed/",
                "card_type": "threat",
                "taxonomy": "vertical-competitor-move",
                "evidence_grade": "light",
                "confidence": "low",
            },
        ]
        return {
            "id": f"fake-{uuid.uuid4()}",
            "model": model,
            "content": json.dumps(
                {
                    "topic": "Construction sector",
                    "horizon_days": 30,
                    "vertical": "Construction",
                    "summary": "s" * 60,
                    "cards": cards,
                }
            ),
            "usage": {"input_tokens": 10, "output_tokens": 10},
            "agent_run_id": agent_run_id,
        }


def _sourced_construction_profile(_profile_id, *, require_urls=True) -> dict[str, Any]:
    return {
        "profile_id": "vertical-construction",
        "function_id": "18-04-vertical-intel-construction",
        "topic": "Construction sector",
        "horizon_days": 30,
        "min_sources": 2,
        "min_distinct_domains": 2,
        "source_chars": 8000,
        "urls": [FABRIC_URL, MONEYWEB_URL],
    }


def test_a_sourced_scanner_scans_and_records_its_cards(clients, monkeypatch):
    """Filling in a profile's urls is all it takes -- no code change."""
    monkeypatch.setattr(dispatch, "_resolve_scan_profile", _sourced_construction_profile)
    monkeypatch.setattr(dispatch, "build_gateway_client", lambda: _CardsGatewayClient())

    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    db.seed(task_id, "vertical-scan-construction")
    dispatch.DISPATCH_TABLE["vertical-scan-construction"](
        task_id, _envelope(task_id, "vertical-scan-construction"), db
    )

    ref = db.get_result_ref(task_id)
    assert db.get_task(task_id)["state"] == "completed"
    assert ref["status"] == "scanned"
    assert ref["card_count"] == 3
    assert ref["vault_signal_id"]
    assert ref["profile_id"] == "vertical-construction"


def test_a_scanners_cards_are_validated_against_its_own_package_schema(clients, monkeypatch):
    """The same runtime contract enforcement ingest-signals got -- against
    whichever package's schema.json this scanner belongs to."""
    monkeypatch.setattr(dispatch, "_resolve_scan_profile", _sourced_construction_profile)
    monkeypatch.setattr(
        dispatch,
        "build_gateway_client",
        lambda: _CardsGatewayClient(
            cards=[
                {
                    "headline": "A card with a taxonomy this package does not define",
                    "so_what": "should be rejected before it reaches the Vault",
                    "source_url": FABRIC_URL,
                    "card_type": "opportunity",
                    "taxonomy": "leadership-change",  # function 11's set, not 18-04's
                    "evidence_grade": "moderate",
                    "confidence": "high",
                }
            ]
        ),
    )

    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    db.seed(task_id, "vertical-scan-construction")

    with pytest.raises(dispatch.DispatchError, match="schema.json validation"):
        dispatch.DISPATCH_TABLE["vertical-scan-construction"](
            task_id, _envelope(task_id, "vertical-scan-construction"), db
        )

    assert not clients._signals


def test_card_batches_are_not_written_as_opportunity_card_rows(clients, monkeypatch):
    """Eleven scanners over three shared listening scopes will surface one
    event several times; writing straight to opportunity_cards would put
    that duplication in the table the morning brief reads. Card rows are
    dedupe's job, and dedupe is still a no-op."""
    monkeypatch.setattr(dispatch, "_resolve_scan_profile", _sourced_construction_profile)
    monkeypatch.setattr(dispatch, "build_gateway_client", lambda: _CardsGatewayClient())

    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    db.seed(task_id, "vertical-scan-construction")
    dispatch.DISPATCH_TABLE["vertical-scan-construction"](
        task_id, _envelope(task_id, "vertical-scan-construction"), db
    )

    assert len(clients._signals) == 1
    assert not clients._opportunity_cards


def test_a_card_batch_feeds_the_same_cross_run_memory(clients, monkeypatch):
    """function 09 emits `signals` and the eleven emit `cards`; both are
    batches of attributed items under a profile topic, so both belong in
    the same memory."""
    monkeypatch.setattr(dispatch, "_resolve_scan_profile", _sourced_construction_profile)
    gateway = _CardsGatewayClient()
    monkeypatch.setattr(dispatch, "build_gateway_client", lambda: gateway)

    db = FakeTaskDB()
    for _ in range(2):
        task_id = str(uuid.uuid4())
        db.seed(task_id, "vertical-scan-construction")
        dispatch.DISPATCH_TABLE["vertical-scan-construction"](
            task_id, _envelope(task_id, "vertical-scan-construction"), db
        )

    assert "Already captured" in gateway.calls[1]["user_content"]
    assert db.get_result_ref(task_id)["repeat_count"] == 3


# ---------------------------------------------------------------------
# Image staging — the failure mode this repo has already hit twice
# ---------------------------------------------------------------------

DOCKERFILE = (
    functions_dir().parent / "services" / "orchestrator" / "Dockerfile"
).read_text(encoding="utf-8")


SCANNER_FUNCTION_IDS = sorted(fid for fid, _profile, _agent in dispatch.SCANNER_TASKS.values())


@pytest.mark.parametrize("function_id", SCANNER_FUNCTION_IDS)
def test_each_scanners_runtime_files_are_staged_into_the_image(function_id):
    """Every scanner reads its own prompt.md (via _read_prompt) and its own
    schema.json (runtime output validation) at dispatch time, from
    functions/ -- which sits outside this image's build context, so each
    file needs an explicit Dockerfile COPY.

    The Dockerfile's own header records this gap biting twice already: once
    for the original handlers, once when PR #84 added eight more
    _read_prompt() call sites without staging any of them. Both times the
    symptom was FileNotFoundError on first real dispatch. This test is that
    check, mechanised."""
    assert f"COPY functions/{function_id}/prompt.md" in DOCKERFILE
    assert f"COPY functions/{function_id}/schema.json" in DOCKERFILE


def test_function_09s_schema_is_staged_too():
    """Runtime schema validation (F-A) reads it on every daily scan."""
    assert "COPY functions/09-market-intelligence-director/schema.json" in DOCKERFILE


def test_the_shared_scan_profiles_file_is_staged():
    assert "COPY functions/_shared/scan-profiles.yaml" in DOCKERFILE


def test_the_source_candidate_register_is_staged():
    """probe_sources_handler reads it on every source-discovery run."""
    assert "COPY functions/_shared/source-candidates.yaml" in DOCKERFILE
