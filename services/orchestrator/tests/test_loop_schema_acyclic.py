"""AC-006: both shipped loop files pass validate_loop_file (schema +
acyclicity); a synthetic cyclic fixture is rejected with CyclicLoopError.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from orchestrator.loop_loader import CyclicLoopError, validate_loop_file

ORCHESTRATOR_DIR = Path(__file__).resolve().parent.parent
GOLDEN_DIR = Path(__file__).resolve().parent / "golden"


def test_daily_signal_loop_valid_and_acyclic():
    loop = validate_loop_file(ORCHESTRATOR_DIR / "loops" / "daily-signal-loop.yaml")
    assert loop.loop_id == "daily-signal-loop"
    # 5 original (ingest/score/draft/qa/publish) + 11 intelligence-squad
    # fan-out scanners + dedupe-signal-cards + competitive-response-strategize
    # + morning-brief-rollup + executive-brief-rollup (session/s10-intelligence)
    # + 3 S8 proof-circuit tasks (draft-linkedin-post/content-qa-review/
    # request-linkedin-approval, session/s8-first-loop, AC-30/AC-31).
    assert len(loop.tasks) == 23


def test_weekly_content_loop_valid_and_acyclic():
    loop = validate_loop_file(ORCHESTRATOR_DIR / "loops" / "weekly-content-loop.yaml")
    assert loop.loop_id == "weekly-content-loop"
    # 1 Monday plan + 2 Tuesday (brief, advocacy-harvest) + 6 Wednesday drafting
    # fan-out (insight-story, ghostwrite, carousel, newsletter, case-study,
    # repurpose) + 12 Thursday per-draft dual-verdict QA gates (brand-steward,
    # fact-check, one pair per Wednesday draft) + 5 Friday per-draft
    # scheduling/publish steps (4 Buffer-eligible drafts + newsletter).
    # ROUND 34 (docs/content-learnings.md, "batch-gating" fix): was 2
    # aggregate Thursday tasks + 1 batch Friday task + 1 newsletter task = 13;
    # now 12 + 4 + 1 = 26, so that one bad draft only dead-letters its own
    # Friday task(s), never a sibling's.
    assert len(loop.tasks) == 26


def test_cyclic_loop_rejected():
    with pytest.raises(CyclicLoopError):
        validate_loop_file(GOLDEN_DIR / "cyclic-loop.yaml")
