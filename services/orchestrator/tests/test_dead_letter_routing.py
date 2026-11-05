"""One Teams card per real failure, not one per task in its blast radius.

Before this, a task that dead-lettered took every task downstream of it
down too (state_machine.cascade_dead_letter), and each of those published
its own DeadLetterAlert and posted its own card: 23 for daily-signal-loop
and 33 for weekly-content-loop, every morning (about 55 a day in the OS
Approvals channel), with the one card that named the real failure
indistinguishable from the rest.

The first group builds the REAL loop graphs from loops/*.yaml, kills a task,
and replays every alert through worker.handle_dead_letter_alert. No Postgres
needed: the db is a small fake that answers the two questions the routing
asks (what reason was recorded, and who depends on this task).
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

import pytest
import yaml
from orchestrator import dead_letter_routing, teams_notify, worker

LOOPS_DIR = Path(__file__).resolve().parent.parent / "loops"

FAILURE = "dead_lettered"
CASCADE = "dependency_dead_lettered"


class FakeDB:
    """Only the three calls dead_letter_routing makes."""

    def __init__(self, tasks: dict[str, dict[str, Any]], reasons: dict[str, str]) -> None:
        self.tasks = tasks
        self.reasons = reasons
        self.dependent_lookups: list[str] = []

    def get_dead_letter_reason(self, task_id: str) -> str | None:
        return self.reasons.get(task_id)

    def find_dependent_tasks(self, task_id: str) -> list[dict[str, Any]]:
        self.dependent_lookups.append(task_id)
        return [t for t in self.tasks.values() if task_id in t["depends_on"]]


def _graph(loop_file: str) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """{uuid: task row} for a real loop, plus {logical task_id: uuid}."""
    definition = yaml.safe_load((LOOPS_DIR / loop_file).read_text())
    ids = {
        t["task_id"]: str(uuid.uuid5(uuid.NAMESPACE_OID, t["task_id"]))
        for t in definition["tasks"]
    }
    rows = {
        ids[t["task_id"]]: {
            "task_id": ids[t["task_id"]],
            "task_type": t["task_type"],
            "loop_id": definition["loop_id"],
            "depends_on": [ids[d] for d in (t.get("depends_on") or [])],
        }
        for t in definition["tasks"]
    }
    return rows, ids


def _alert(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "alert_version": "1",
        "task_id": row["task_id"],
        "task_type": row["task_type"],
        "loop_id": row["loop_id"],
        "failure_count": 3,
        "dead_lettered_at": "2026-11-01T04:01:25+00:00",
    }


@pytest.fixture()
def posts(monkeypatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        teams_notify, "notify_dead_letter", lambda **kwargs: calls.append(kwargs) or True
    )
    return calls


@pytest.mark.parametrize(
    "loop_file, root_logical_id",
    [
        ("daily-signal-loop.yaml", "ingest"),
        ("weekly-content-loop.yaml", "monday-plan-content"),
    ],
)
def test_a_dead_root_posts_one_card_for_the_whole_loop(loop_file, root_logical_id, posts, caplog):
    """THE incident: root fails, every other task cascades. 23 and 33 cards
    became 1, and that one says how many tasks it took down."""
    rows, ids = _graph(loop_file)
    root = ids[root_logical_id]
    reasons = {tid: CASCADE for tid in rows}
    reasons[root] = FAILURE
    fake = FakeDB(rows, reasons)

    with caplog.at_level(logging.INFO):
        for row in rows.values():
            worker.handle_dead_letter_alert(_alert(row), fake)

    assert len(posts) == 1, f"{len(rows)} alerts should produce one card, got {len(posts)}"
    assert posts[0]["task_id"] == root
    assert posts[0]["downstream_total"] == len(rows) - 1
    assert sum(posts[0]["downstream_by_type"].values()) == len(rows) - 1

    # The Azure alert rule counts this log line. It must still be one per
    # dead task, cascades included.
    logged = [r for r in caplog.records if "dead_letter_alert_received" in r.getMessage()]
    assert len(logged) == len(rows)


def test_a_real_failure_partway_down_the_graph_counts_only_what_it_blocks(posts):
    rows, ids = _graph("weekly-content-loop.yaml")
    failed = ids["tuesday-research-brief"]
    blocked = {
        tid
        for tid, row in rows.items()
        if tid != failed and failed in _ancestors(rows, tid)
    }
    reasons = {tid: CASCADE for tid in blocked}
    reasons[failed] = FAILURE
    fake = FakeDB(rows, reasons)

    for tid in [failed, *blocked]:
        worker.handle_dead_letter_alert(_alert(rows[tid]), fake)

    assert len(posts) == 1
    assert posts[0]["task_id"] == failed
    assert posts[0]["downstream_total"] == len(blocked)
    assert 0 < len(blocked) < len(rows) - 1, "the root and its other branch were not touched"


def test_independent_real_failures_each_keep_their_own_card(posts):
    """Suppressing cascades must never hide a second, separate failure."""
    rows, ids = _graph("daily-signal-loop.yaml")
    a, b = ids["competitor-discovery-scan"], ids["fabric-ecosystem-scout"]
    fake = FakeDB(rows, {a: FAILURE, b: FAILURE})

    worker.handle_dead_letter_alert(_alert(rows[a]), fake)
    worker.handle_dead_letter_alert(_alert(rows[b]), fake)

    assert [p["task_id"] for p in posts] == [a, b]


def _ancestors(rows: dict[str, dict[str, Any]], task_id: str) -> set[str]:
    seen: set[str] = set()
    stack = list(rows[task_id]["depends_on"])
    while stack:
        current = stack.pop()
        if current not in seen:
            seen.add(current)
            stack.extend(rows[current]["depends_on"])
    return seen


# --- describe(): the pieces ------------------------------------------------


def _task(task_id: str, task_type: str, depends_on: list[str]) -> dict[str, Any]:
    return {"task_id": task_id, "task_type": task_type, "loop_id": "l", "depends_on": depends_on}


def test_a_cascade_is_recognised_without_walking_the_graph():
    fake = FakeDB({"a": _task("a", "x", [])}, {"a": CASCADE})
    context = dead_letter_routing.describe("a", fake)
    assert context.kind == dead_letter_routing.KIND_CASCADE
    assert fake.dependent_lookups == []


def test_a_diamond_counts_each_blocked_task_once():
    tasks = {
        "root": _task("root", "r", []),
        "left": _task("left", "l", ["root"]),
        "right": _task("right", "l", ["root"]),
        "join": _task("join", "j", ["left", "right"]),
    }
    context = dead_letter_routing.describe("root", FakeDB(tasks, {"root": FAILURE}))
    assert context.kind == dead_letter_routing.KIND_FAILURE
    assert context.downstream_total == 3
    assert context.downstream_by_type == {"l": 2, "j": 1}


def test_a_leaf_failure_reports_zero_downstream():
    fake = FakeDB({"leaf": _task("leaf", "x", [])}, {"leaf": FAILURE})
    context = dead_letter_routing.describe("leaf", fake)
    assert (context.kind, context.downstream_total) == (dead_letter_routing.KIND_FAILURE, 0)


def test_a_malformed_cycle_terminates():
    tasks = {"a": _task("a", "x", ["b"]), "b": _task("b", "x", ["a"])}
    context = dead_letter_routing.describe("a", FakeDB(tasks, {"a": FAILURE}))
    assert context.downstream_total == 1


@pytest.mark.parametrize("reason", [None, "something_new"])
def test_an_unrecorded_or_unknown_reason_is_unknown(reason):
    fake = FakeDB({"a": _task("a", "x", [])}, {} if reason is None else {"a": reason})
    assert dead_letter_routing.describe("a", fake) is dead_letter_routing.UNKNOWN


class _BrokenDB:
    def get_dead_letter_reason(self, task_id: str) -> str:
        raise ConnectionError("postgres unreachable")


def test_a_lookup_failure_fails_open_and_still_posts_the_original_card(posts):
    """A noisy channel is recoverable; a failure nobody hears about is not."""
    body = {
        "alert_version": "1",
        "task_id": "t-1",
        "task_type": "ingest-signals",
        "loop_id": "daily-signal-loop",
        "failure_count": 3,
    }
    assert worker.handle_dead_letter_alert(body, _BrokenDB()) is True
    assert posts == [
        {
            "task_id": "t-1",
            "task_type": "ingest-signals",
            "loop_id": "daily-signal-loop",
            "failure_count": 3,
        }
    ]


def test_a_db_without_the_new_method_fails_open(posts):
    """Older fakes and any db object lacking get_dead_letter_reason."""
    body = {
        "alert_version": "1",
        "task_id": "t-1",
        "task_type": "x",
        "loop_id": "l",
        "failure_count": 3,
    }
    worker.handle_dead_letter_alert(body, object())
    assert len(posts) == 1 and "downstream_total" not in posts[0]
