"""Tests for the source promotion pipeline (F-SOURCE-DISCOVERY).

Most scanner profiles shipped without urls because nobody had written down
where to read each sector. The obvious fix -- let the system find its own
sources -- runs into a circularity: a candidate cannot be evaluated
without fetching it, and fetching it requires allow-listing, which is the
decision the evaluation exists to inform.

The pipeline splits the capability. probe_url reads a SEPARATE allow-list
and returns shape, never a body, so probing is strictly smaller than
scanning. Scoring is deterministic. And promotion is never automatic: the
handler ends at a real gate-check carrying the detail and the reasoning,
and a person makes the edit.

The security property these tests exist to hold down is that last one --
nothing in this pipeline widens the egress allow-list on its own.
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

CANDIDATES_PATH = functions_dir() / "_shared" / "source-candidates.yaml"


def _probe(**overrides: Any) -> dict[str, Any]:
    probe = {
        "status_code": 200,
        "is_feed": True,
        "item_count": 20,
        "extractable_chars": 4000,
        "sample_titles": ["A headline", "Another headline"],
    }
    probe.update(overrides)
    return probe


# ---------------------------------------------------------------------
# Scoring — deterministic, and every component is something measured
# ---------------------------------------------------------------------


def test_an_unreachable_source_scores_zero_and_says_why():
    score, reasons = dispatch._score_probe(_probe(status_code=404))

    assert score == 0.0
    assert "HTTP 404" in reasons[0]


def test_a_healthy_feed_scores_well_enough_to_recommend():
    score, reasons = dispatch._score_probe(_probe())

    assert score >= dispatch.PROMOTE_SCORE
    assert dispatch._promotion_verdict(score) == "recommend_promote"
    assert any("parses as a feed" in reason for reason in reasons)


def test_a_thin_source_is_held_for_review_rather_than_recommended():
    """Reachable but nearly empty: the arithmetic should not pretend to
    have decided."""
    score, reasons = dispatch._score_probe(
        _probe(is_feed=False, item_count=0, extractable_chars=120, sample_titles=[])
    )

    assert dispatch._promotion_verdict(score) in {"needs_review", "recommend_reject"}
    assert any("below the" in reason for reason in reasons)


def test_every_reason_names_something_the_probe_measured():
    """The reasons go on the approval card verbatim, so a reviewer sees
    WHY a number came out as it did rather than being asked to trust it."""
    _score, reasons = dispatch._score_probe(_probe(item_count=3))

    assert any("HTTP 200" in r or "reachable" in r for r in reasons)
    assert any("3 item" in r for r in reasons)
    assert any("characters of extractable text" in r for r in reasons)


def test_verdict_thresholds_are_ordered():
    assert dispatch.REJECT_SCORE < dispatch.PROMOTE_SCORE
    assert dispatch._promotion_verdict(dispatch.PROMOTE_SCORE) == "recommend_promote"
    assert dispatch._promotion_verdict(dispatch.REJECT_SCORE - 0.01) == "recommend_reject"


# ---------------------------------------------------------------------
# The candidate register
# ---------------------------------------------------------------------


def test_candidates_are_well_formed_and_cite_where_they_came_from():
    for candidate in dispatch._load_source_candidates():
        assert candidate["candidate_id"]
        assert candidate["url"].startswith("https://")
        assert candidate["profile_id"]
        # Nothing invented: every entry says which prompt or positioning
        # section proposed it.
        assert len(candidate["rationale"]) > 30, candidate["candidate_id"]


def test_every_candidate_targets_a_profile_that_exists():
    profiles = yaml.safe_load(
        (functions_dir() / "_shared" / "scan-profiles.yaml").read_text(encoding="utf-8")
    )
    known = {profile["profile_id"] for profile in profiles["profiles"]}

    for candidate in dispatch._load_source_candidates():
        assert candidate["profile_id"] in known, candidate["candidate_id"]


# ---------------------------------------------------------------------
# The approval card
# ---------------------------------------------------------------------


def _results() -> list[dict[str, Any]]:
    return [
        {
            "candidate_id": "itweb-sa-tech-news",
            "profile_id": "competitor-discovery",
            "url": "https://www.itweb.co.za/rss/news",
            "host": "www.itweb.co.za",
            "rationale": "Named in function 10's Method as SA IT trade press.",
            "score": 0.9,
            "verdict": "recommend_promote",
            "reasons": ["reachable (HTTP 200)", "parses as a feed with 20 item(s)"],
            "probe": _probe(sample_titles=["Vendor X ships a thing"]),
        }
    ]


def test_the_card_states_what_approving_it_authorises():
    """A person is being asked to widen an egress allow-list. The card must
    say so, in those terms."""
    evidence = dispatch._render_promotion_evidence(_results())

    assert "egress allow-list" in evidence
    assert "Approving this card authorises" in evidence


def test_the_card_carries_the_detail_list_and_the_reasoning():
    evidence = dispatch._render_promotion_evidence(_results())

    assert "itweb-sa-tech-news" in evidence
    assert "competitor-discovery" in evidence
    assert "www.itweb.co.za" in evidence
    assert "0.9" in evidence
    assert "recommend promote" in evidence
    assert "parses as a feed with 20 item(s)" in evidence
    assert "Named in function 10's Method" in evidence


def test_the_card_shows_sample_titles_as_evidence_of_what_the_source_carries():
    """A probe that says "200, feed, 20 items" cannot tell a reviewer
    whether those items are about their market."""
    evidence = dispatch._render_promotion_evidence(_results())

    assert "Vendor X ships a thing" in evidence


def test_the_card_says_no_content_was_fetched_into_a_scan():
    evidence = dispatch._render_promotion_evidence(_results())

    assert "metadata only" in evidence


# ---------------------------------------------------------------------
# The handler, end to end
# ---------------------------------------------------------------------


class _ProbingMCPClient:
    def __init__(self, per_url: dict[str, dict[str, Any]] | None = None) -> None:
        self.per_url = per_url or {}
        self.tools_called: list[str] = []

    def __enter__(self) -> "_ProbingMCPClient":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        pass

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.tools_called.append(tool_name)
        return self.per_url.get(arguments.get("url", ""), _probe())


@pytest.fixture()
def clients(monkeypatch):
    return patch_dispatch_clients(monkeypatch)


# A candidate that is NOT live on any scan profile -- i.e. one the probe
# still has a decision to make about.
#
# The handler tests below used to run against the real register, which
# worked only for as long as it held something unpromoted. It does not:
# every entry in source-candidates.yaml was promoted on 2 Sep 2026, so
# reading the real file here would exercise the nothing-to-probe path and
# assert nothing about the card. `_pending_source_candidates` still runs
# for real against the real scan-profiles.yaml -- only the register side
# is seeded, so the promoted/pending split itself is under test.
PENDING_CANDIDATE = {
    "candidate_id": "pending-example-feed",
    "profile_id": "vertical-construction",
    "url": "https://example.invalid/feed",
    "rationale": "A candidate awaiting its first probe.",
}


@pytest.fixture()
def pending_register(monkeypatch):
    monkeypatch.setattr(dispatch, "_load_source_candidates", lambda: [dict(PENDING_CANDIDATE)])
    return [dict(PENDING_CANDIDATE)]


def _run(db: FakeTaskDB) -> str:
    task_id = str(uuid.uuid4())
    db.seed(task_id, "probe-sources")
    dispatch.probe_sources_handler(task_id, _envelope(task_id, "probe-sources"), db)
    return task_id


def test_probing_uses_probe_url_never_fetch_url(clients, monkeypatch, pending_register):
    """The sandbox boundary, asserted at the call site: this pipeline must
    not reach for the scan-path tool."""
    mcp = _ProbingMCPClient()
    monkeypatch.setattr(dispatch, "build_mcp_web_client", lambda: mcp)

    _run(FakeTaskDB())

    assert set(mcp.tools_called) == {"probe_url"}


def test_the_run_raises_one_approval_card_and_completes(clients, monkeypatch, pending_register):
    monkeypatch.setattr(dispatch, "build_mcp_web_client", lambda: _ProbingMCPClient())

    db = FakeTaskDB()
    task_id = _run(db)

    ref = db.get_result_ref(task_id)
    assert db.get_task(task_id)["state"] == "completed"
    assert ref["probe_batch_id"]
    assert ref["probed_count"] == len(pending_register)
    assert ref["recommended_candidate_ids"]
    assert ref["content_hash"]


def test_the_gate_check_is_the_configure_action_not_a_publish(
    clients, monkeypatch, pending_register
):
    """Promotion is a configuration change under its own autonomy entry --
    it must never borrow a publish function_id."""
    monkeypatch.setattr(dispatch, "build_mcp_web_client", lambda: _ProbingMCPClient())
    calls: list[dict[str, Any]] = []

    class _RecordingGatekeeper:
        def __enter__(self) -> "_RecordingGatekeeper":
            return self

        def __exit__(self, *_exc_info: object) -> None:
            pass

        def gate_check(self, **kw: Any) -> dict[str, Any]:
            calls.append(kw)
            return {"decision_id": "d", "outcome": "queued", "approval_id": "a"}

    monkeypatch.setattr(dispatch, "build_gatekeeper_client", lambda: _RecordingGatekeeper())

    _run(FakeTaskDB())

    assert calls[0]["function_id"] == "config.source_promotion"
    assert calls[0]["action_class"] == "configure"
    assert "egress allow-list" in calls[0]["evidence_summary"]
    assert calls[0]["preview_title"].startswith("Source promotion")


def test_an_unreachable_candidate_is_a_result_not_a_failure(clients, monkeypatch, pending_register):
    """One dead candidate must not sink the review -- it is exactly the
    finding the weekly re-probe exists to surface."""

    class _FailingMCPClient(_ProbingMCPClient):
        def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
            raise RuntimeError("probe failed (test)")

    monkeypatch.setattr(dispatch, "build_mcp_web_client", lambda: _FailingMCPClient())

    db = FakeTaskDB()
    task_id = _run(db)

    assert db.get_task(task_id)["state"] == "completed"
    assert db.get_result_ref(task_id)["recommended_candidate_ids"] == []


# ---------------------------------------------------------------------
# Already-promoted candidates — the weekly duplicate-card defect
# ---------------------------------------------------------------------
#
# Promotion never retires an entry from source-candidates.yaml (on
# purpose: the register is the record of what was proposed and why, and
# the card names the candidate_id). The handler used to probe the whole
# register regardless, so once every candidate had been promoted -- which
# is exactly where 2 Sep 2026 left the real file -- every weekly run
# raised a config.source_promotion card recommending promotions that had
# already happened.


def test_a_promoted_candidate_is_not_re_probed(clients, monkeypatch):
    """The split is on the candidate's url appearing on a live profile."""
    promoted_url = "https://www.itweb.co.za/rss/news"
    assert promoted_url in dispatch._promoted_source_urls(), (
        "this test's premise is that the real register holds a promoted url"
    )
    monkeypatch.setattr(
        dispatch,
        "_load_source_candidates",
        lambda: [
            {
                "candidate_id": "already-live",
                "profile_id": "competitor-discovery",
                "url": promoted_url,
            },
            dict(PENDING_CANDIDATE),
        ],
    )
    mcp = _ProbingMCPClient()
    monkeypatch.setattr(dispatch, "build_mcp_web_client", lambda: mcp)

    db = FakeTaskDB()
    task_id = _run(db)

    ref = db.get_result_ref(task_id)
    assert ref["probed_count"] == 1
    assert ref["already_promoted_count"] == 1
    assert ref["recommended_candidate_ids"] == ["pending-example-feed"]
    assert mcp.tools_called == ["probe_url"], "the promoted candidate was probed anyway"


def test_the_card_says_what_it_skipped_and_why():
    """A reviewer who knows the register holds seven entries and reads
    "2 probed" must not be left guessing what happened to the other five."""
    evidence = dispatch._render_promotion_evidence(_results(), already_promoted=5)

    assert "5 further candidate(s) in the register are already live" in evidence
    assert "re-approve" in evidence


def test_the_card_stays_silent_about_skipping_when_nothing_was_skipped():
    assert "already live" not in dispatch._render_promotion_evidence(_results())


def test_a_fully_promoted_register_raises_no_card_at_all(clients, monkeypatch):
    """The defect, stated as behaviour. With nothing left to decide the run
    must complete quietly -- an approval card a reviewer cannot act on is
    worse than no card, because it teaches them to close the next one
    unread."""
    calls: list[dict[str, Any]] = []

    class _RecordingGatekeeper:
        def __enter__(self) -> "_RecordingGatekeeper":
            return self

        def __exit__(self, *_exc_info: object) -> None:
            pass

        def gate_check(self, **kw: Any) -> dict[str, Any]:
            calls.append(kw)
            return {"decision_id": "d", "outcome": "queued", "approval_id": "a"}

    monkeypatch.setattr(dispatch, "build_gatekeeper_client", lambda: _RecordingGatekeeper())

    def _explode():
        raise AssertionError("a fully promoted register must not reach mcp-web")

    monkeypatch.setattr(dispatch, "build_mcp_web_client", _explode)

    db = FakeTaskDB()
    task_id = _run(db)

    assert db.get_task(task_id)["state"] == "completed"
    assert db.get_result_ref(task_id)["status"] == "nothing_to_probe"
    assert calls == [], "no gate-check may be raised when nothing is pending"


def test_the_real_register_is_currently_fully_promoted(clients):
    """Documents the state that made the defect live, and fails usefully
    when it changes: once a new candidate is added, the run raises a real
    card again and this test should be updated to say so rather than
    deleted."""
    pending, already = dispatch._pending_source_candidates()

    assert not pending
    assert len(already) == len(dispatch._load_source_candidates())


def test_an_empty_register_file_is_still_an_error_not_a_quiet_completion(clients, monkeypatch):
    """A finished promotion round and a broken config file must not look
    the same from /status."""
    monkeypatch.setattr(dispatch, "_load_source_candidates", list)

    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    db.seed(task_id, "probe-sources")

    with pytest.raises(dispatch.DispatchError, match="lists no candidates"):
        dispatch.probe_sources_handler(task_id, _envelope(task_id, "probe-sources"), db)


def test_the_pipeline_never_edits_a_scan_profile_or_the_allowlist(clients, monkeypatch):
    """The security property. A run must leave both files byte-identical:
    promotion is a person's edit after approving the card."""
    monkeypatch.setattr(dispatch, "build_mcp_web_client", lambda: _ProbingMCPClient())
    profiles_path = functions_dir() / "_shared" / "scan-profiles.yaml"
    bicep_path = functions_dir().parent / "infra" / "main.bicep"
    before = (profiles_path.read_bytes(), bicep_path.read_bytes())

    _run(FakeTaskDB())

    assert (profiles_path.read_bytes(), bicep_path.read_bytes()) == before


def test_probe_batches_are_not_treated_as_reported_signals(clients, monkeypatch):
    """A probe is evidence about a source, not a signal about the market --
    cross-run memory must never recall one as something already reported."""
    assert dispatch.PROBE_BATCH_TYPE not in dispatch.SCAN_BATCH_TYPES
