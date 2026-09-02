"""The brief must say when most of the market is not being watched.

Eleven of the twelve scan profiles carry no source urls, so their
scanners take _complete_unconfigured_scan's path: a WARNING, a result_ref
of status=not_configured, and then COMPLETED. That is the right outcome
for the task -- an unsourced profile is a known gap, not a reason to fail
the loop or to sink the scans that did run -- but it means anything asking
"did the daily loop succeed?" sees green across the board.

Before this, the only human-facing trace was one line in the brief:

    "No cards. Every scanner either found nothing or has no sources
     configured."

which conflates the two states a reader most needs told apart. A quiet
market is a fact about the market. A dormant scanner is unfinished setup,
and it will keep reading nothing tomorrow. These tests pin that the brief
now states coverage, names the dormant profiles, and stays quiet when
every scanner is live.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from orchestrator import dispatch
from tests.fakes import patch_dispatch_clients
from tests.test_dispatch import FakeTaskDB, _envelope


def _ref(**overrides: Any) -> dict[str, Any]:
    base = {
        "cards": [],
        "scanners_read": 0,
        "cards_in": 0,
        "cards_out": 0,
        "configured_count": 2,
        "dormant_count": 11,
        "scanner_total": 13,
        "dormant_profiles": ["vertical-construction", "vertical-mining-industrial"],
    }
    return {**base, **overrides}


class _StubDB:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def get_task(self, task_id: str) -> dict[str, Any]:
        return {"task_id": task_id, "depends_on": [r["task_id"] for r in self._rows]}

    def get_tasks(self, task_ids: list[str]) -> list[dict[str, Any]]:
        return [r for r in self._rows if r["task_id"] in task_ids]


def _scanner(task_id: str, *, profile_id: str, configured: bool) -> dict[str, Any]:
    ref = (
        {"vault_signal_id": f"sig-{task_id}", "profile_id": profile_id}
        if configured
        else {"status": "not_configured", "profile_id": profile_id}
    )
    return {"task_id": task_id, "task_type": f"{profile_id}-scan", "result_ref": ref}


def test_coverage_counts_configured_and_dormant_scanners_separately():
    db = _StubDB(
        [
            _scanner("a", profile_id="competitor-discovery", configured=True),
            _scanner("b", profile_id="vertical-construction", configured=False),
            _scanner("c", profile_id="vertical-mining-industrial", configured=False),
        ]
    )

    coverage = dispatch._scanner_coverage("rollup", db)

    assert coverage["configured_count"] == 1
    assert coverage["dormant_count"] == 2
    assert coverage["scanner_total"] == 3
    assert coverage["dormant_profiles"] == [
        "vertical-construction",
        "vertical-mining-industrial",
    ]


def test_the_brief_states_coverage_and_names_the_dormant_profiles():
    full, executive = dispatch._render_intel_brief(_ref(), {})

    for text in (full, executive):
        assert "2 of 13 scanners configured" in text
        assert "11 dormant, awaiting sources" in text
        # The distinction the whole change exists to make.
        assert "it is not a quiet market" in text
    assert "vertical-construction" in full


def test_a_fully_configured_morning_says_nothing_about_coverage():
    """A line that shows up every day regardless is a line people stop
    seeing. Coverage is reported only when some of it is missing."""
    ref = _ref(configured_count=13, dormant_count=0, dormant_profiles=[])

    full, executive = dispatch._render_intel_brief(ref, {})

    assert "Coverage:" not in full
    assert "Coverage:" not in executive


def test_no_cards_from_dormant_scanners_does_not_read_as_a_quiet_market():
    ref = _ref(configured_count=0, dormant_count=13, dormant_profiles=[])

    full, _ = dispatch._render_intel_brief(ref, {})

    assert "none were possible" in full
    assert "Nothing scanned the market today." in full


def test_no_cards_with_every_scanner_live_reads_as_a_quiet_market():
    ref = _ref(configured_count=13, dormant_count=0, dormant_profiles=[])

    full, _ = dispatch._render_intel_brief(ref, {})

    assert "Every configured scanner ran and found nothing." in full
    assert "dormant" not in full


def test_a_mixed_morning_splits_the_two_reasons():
    ref = _ref(configured_count=2, dormant_count=11, scanner_total=13)

    full, _ = dispatch._render_intel_brief(ref, {})

    assert "2 configured scanner(s) found nothing" in full
    assert "the other 11 are dormant and did not look" in full


def test_coverage_is_silent_when_the_dedupe_ref_predates_this_change():
    """An older result_ref carries none of these keys; the brief must
    render exactly as it used to rather than claiming 0 of 0."""
    ref = {"cards": [], "scanners_read": 0, "cards_in": 0, "cards_out": 0}

    full, executive = dispatch._render_intel_brief(ref, {})

    assert "Coverage:" not in full
    assert "Coverage:" not in executive
    assert "Every configured scanner ran and found nothing." in full


# --- the real path ---------------------------------------------------------
#
# Everything above hands _render_intel_brief a cards_ref built by the test.
# That is exactly how an earlier bug in this session hid: the QA gate was
# fed a hand-seeded result_ref, so a key the real handler dropped still
# looked present. These walk it for real -- a dormant scanner produced by
# _complete_unconfigured_scan, through the real dedupe handler, into the
# real brief -- so nothing here can pass on a shape the pipeline never
# actually produces.

@pytest.fixture()
def clients(monkeypatch):
    return patch_dispatch_clients(monkeypatch)


def test_a_dormant_scanner_reaches_the_brief_through_the_real_handlers(clients, monkeypatch):
    db = FakeTaskDB()

    # One real scanner that cannot run: its profile has no urls, so the
    # real handler takes _complete_unconfigured_scan's path itself.
    dormant_id = str(uuid.uuid4())
    db.seed(dormant_id, "vertical-scan-construction")
    monkeypatch.setattr(
        dispatch,
        "_resolve_scan_profile",
        lambda profile_id, require_urls=True: {
            "profile_id": profile_id,
            "topic": "t",
            "horizon_days": 30,
            "urls": [],
        },
    )
    dispatch.DISPATCH_TABLE["vertical-scan-construction"](
        dormant_id, _envelope(dormant_id, "vertical-scan-construction"), db
    )

    # It completed -- that is the behaviour being kept, and the reason the
    # gap was invisible.
    assert db.get_task(dormant_id)["state"] == "completed"
    assert db.get_result_ref(dormant_id)["status"] == "not_configured"

    dedupe_id = str(uuid.uuid4())
    db.seed(dedupe_id, "dedupe-signal-cards", depends_on=[dormant_id])
    dispatch.DISPATCH_TABLE["dedupe-signal-cards"](
        dedupe_id, _envelope(dedupe_id, "dedupe-signal-cards"), db
    )

    ref = db.get_result_ref(dedupe_id)
    assert ref["dormant_count"] == 1
    assert ref["configured_count"] == 0
    # The PROFILE id, not the task type -- it is the key someone edits in
    # scan-profiles.yaml to make this scanner live, so it is the useful
    # name to print. Asserted here because the real handler chooses it;
    # a hand-seeded ref would have let the wrong one through.
    assert ref["dormant_profiles"] == ["vertical-construction"]

    full, executive = dispatch._render_intel_brief(ref, {})
    assert "0 of 1 scanners configured" in full
    assert "0 of 1 scanners configured" in executive
    assert "Nothing scanned the market today." in full
