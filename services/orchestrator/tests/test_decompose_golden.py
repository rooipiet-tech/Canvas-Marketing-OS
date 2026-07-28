"""AC-009: decomposition is deterministic and matches the checked-in
golden file (task_type, ordering, depends_on edges — ids may differ per
AC-009's wording, so comparison is via source_task_id, not the generated
uuid). Also asserts two calls with identical input yield identical
task_ids too (determinism).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from orchestrator import decompose
from orchestrator.loop_loader import load_loop
from orchestrator.models import HeartbeatEvent

ORCHESTRATOR_DIR = Path(__file__).resolve().parent.parent
GOLDEN_DIR = Path(__file__).resolve().parent / "golden"


@pytest.mark.parametrize(
    "loop_file,heartbeat_file,expected_file",
    [
        (
            "daily-signal-loop.yaml",
            "daily-signal-loop.heartbeat.json",
            "daily-signal-loop.expected.json",
        ),
        (
            "weekly-content-loop.yaml",
            "weekly-content-loop.heartbeat.json",
            "weekly-content-loop.expected.json",
        ),
    ],
)
def test_decompose_matches_golden(loop_file, heartbeat_file, expected_file):
    loop = load_loop(ORCHESTRATOR_DIR / "loops" / loop_file)
    heartbeat_raw = json.loads((GOLDEN_DIR / heartbeat_file).read_text(encoding="utf-8"))
    heartbeat = HeartbeatEvent.model_validate(heartbeat_raw)
    expected = json.loads((GOLDEN_DIR / expected_file).read_text(encoding="utf-8"))

    result = decompose.decompose(loop, heartbeat)

    assert len(result) == len(expected["tasks"])

    # order + task_type + depends_on-shape (by source_task_id, not uuid)
    for actual_task, expected_task in zip(result, expected["tasks"]):
        assert actual_task["source_task_id"] == expected_task["source_task_id"]
        assert actual_task["task_type"] == expected_task["task_type"]

    by_source = {t["source_task_id"]: t for t in result}
    for expected_task in expected["tasks"]:
        actual_task = by_source[expected_task["source_task_id"]]
        actual_dep_sources = [
            by_source_reverse(by_source, dep_uuid) for dep_uuid in actual_task["depends_on"]
        ]
        assert sorted(actual_dep_sources) == sorted(expected_task["depends_on"])


def by_source_reverse(by_source: dict, dep_uuid: str) -> str:
    for source_task_id, task in by_source.items():
        if task["task_id"] == dep_uuid:
            return source_task_id
    raise AssertionError(f"depends_on uuid {dep_uuid!r} does not match any task in the batch")


def test_decompose_is_deterministic():
    loop = load_loop(ORCHESTRATOR_DIR / "loops" / "daily-signal-loop.yaml")
    heartbeat_raw = json.loads(
        (GOLDEN_DIR / "daily-signal-loop.heartbeat.json").read_text(encoding="utf-8")
    )
    heartbeat = HeartbeatEvent.model_validate(heartbeat_raw)

    result_1 = decompose.decompose(loop, heartbeat)
    result_2 = decompose.decompose(loop, heartbeat)

    assert result_1 == result_2
