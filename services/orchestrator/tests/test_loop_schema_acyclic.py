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
    assert len(loop.tasks) == 5


def test_weekly_content_loop_valid_and_acyclic():
    loop = validate_loop_file(ORCHESTRATOR_DIR / "loops" / "weekly-content-loop.yaml")
    assert loop.loop_id == "weekly-content-loop"
    assert len(loop.tasks) == 5


def test_cyclic_loop_rejected():
    with pytest.raises(CyclicLoopError):
        validate_loop_file(GOLDEN_DIR / "cyclic-loop.yaml")
