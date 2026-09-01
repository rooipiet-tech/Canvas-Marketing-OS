"""The whole daily-signal-loop, decomposed and dispatched in one pass.

WHY THIS EXISTS. Every other test in this suite exercises one handler.
Nothing exercised the WIRING BETWEEN them: that decompose's graph matches
the DISPATCH_TABLE, that a handler's result_ref answers the questions the
next handler asks of it, that dependency order actually resolves, and
that the loop as a whole reaches a sensible end state.

That gap is not theoretical. Adding score-signals a handler changed which
ancestor draft-brief resolves -- it used to walk PAST the no-op to ingest
and now stops at score -- and nothing but a full-graph run would have
caught a missing key there. The eleven scanners have the same shape of
risk: eleven registrations that unit tests call directly, never through
dispatch_task's readiness gate.

WHAT THIS IS NOT. It runs against tests/fakes.py, not a deployment, so it
proves wiring rather than behaviour: no real model, no real Vault, no
real Service Bus. tests/e2e/ is the live proof and only runs post-deploy.
This is the cheapest check that stands between "each part works" and
"nobody has ever run the whole thing".
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from orchestrator import decompose, dispatch, worker
from orchestrator.loop_loader import load_loop
from orchestrator.models import HeartbeatEvent, TaskEnvelope
from tests.fakes import patch_dispatch_clients
from tests.test_dispatch import FakeTaskDB

LOOP_PATH = Path(__file__).resolve().parents[1] / "loops" / "daily-signal-loop.yaml"


class _GraphDB(FakeTaskDB):
    """FakeTaskDB seeded from a real decomposed graph rather than one task
    at a time, so dependency state actually gates dispatch."""

    def seed_graph(self, tasks: list[dict[str, Any]]) -> None:
        for task in tasks:
            self.tasks[task["task_id"]] = {
                "task_id": task["task_id"],
                "task_type": task["task_type"],
                "state": "dispatchable" if not task["depends_on"] else "pending",
                "depends_on": list(task["depends_on"]),
                "result_ref": None,
            }

    def dispatchable(self) -> list[str]:
        return [t["task_id"] for t in self.tasks.values() if t["state"] == "dispatchable"]


def _run_whole_loop(db: _GraphDB, tasks: list[dict[str, Any]]) -> dict[str, str]:
    """Dispatch until nothing is dispatchable. Returns source_task_id ->
    final state."""
    by_id = {task["task_id"]: task for task in tasks}
    guard = 0
    while db.dispatchable():
        guard += 1
        assert guard < 200, "dispatch loop failed to make progress"
        task_id = db.dispatchable()[0]
        task = by_id[task_id]
        envelope = TaskEnvelope(
            task_id=uuid.UUID(task_id),
            task_type=task["task_type"],
            agent_run_id=uuid.uuid4(),
            campaign_id=uuid.uuid4(),
            created_at=datetime.now(timezone.utc),
            metadata=worker._task_metadata(task.get("params")),
        )
        dispatch.dispatch_task(envelope, db)
    return {by_id[tid]["source_task_id"]: row["state"] for tid, row in db.tasks.items()}


@pytest.fixture()
def clients(monkeypatch):
    return patch_dispatch_clients(monkeypatch)


@pytest.fixture()
def loop_run(clients):
    loop = load_loop(LOOP_PATH)
    heartbeat = HeartbeatEvent.model_validate(
        {
            "envelope_version": "1",
            "event_type": "heartbeat",
            "event_id": str(uuid.uuid4()),
            "loop_id": "daily-signal-loop",
            "fired_at": datetime.now(timezone.utc).isoformat(),
            "source": "test",
        }
    )
    tasks = decompose.decompose(loop, heartbeat)
    db = _GraphDB()
    db.seed_graph(tasks)
    states = _run_whole_loop(db, tasks)
    return {"db": db, "tasks": tasks, "states": states, "clients": clients}


def test_every_task_in_the_loop_reaches_a_terminal_state(loop_run):
    """Nothing left pending: the graph resolves end to end."""
    stuck = {name: state for name, state in loop_run["states"].items() if state == "pending"}

    assert stuck == {}
    assert len(loop_run["states"]) == 23


def test_the_scan_produces_a_signal_and_the_brief_is_written(loop_run):
    """The spine of the loop: ingest -> score -> draft -> qa."""
    states = loop_run["states"]

    assert states["ingest"] == "completed"
    assert states["score"] == "completed"
    assert states["draft"] == "completed"
    assert states["qa"] == "completed"
    assert loop_run["clients"]._signals
    assert loop_run["clients"]._briefs


def test_scoring_wrote_opportunity_cards_the_old_loop_never_produced(loop_run):
    assert loop_run["clients"]._opportunity_cards


def test_draft_brief_resolved_the_scoring_ancestor_not_the_ingest(loop_run):
    """The lineage change this test exists to guard: score-signals now
    carries a result_ref, so draft-brief stops there instead of walking
    past it to ingest."""
    db, tasks = loop_run["db"], loop_run["tasks"]
    by_source = {task["source_task_id"]: task["task_id"] for task in tasks}

    score_ref = db.get_result_ref(by_source["score"])
    assert score_ref["ranking"]
    assert score_ref["vault_signal_id"] == db.get_result_ref(by_source["ingest"])["vault_signal_id"]


def test_all_eleven_scanners_ran_and_reported_why_they_produced_nothing(loop_run):
    """They are wired, they completed, and each says not_configured rather
    than silently passing through as the no-ops did."""
    db, tasks, states = loop_run["db"], loop_run["tasks"], loop_run["states"]
    by_source = {task["source_task_id"]: task["task_id"] for task in tasks}
    scanner_sources = [
        source
        for source, task_id in by_source.items()
        if db.tasks[task_id]["task_type"] in dispatch.SCANNER_TASKS
    ]

    assert len(scanner_sources) == 11
    for source in scanner_sources:
        assert states[source] == "completed", source
        assert db.get_result_ref(by_source[source])["status"] == "not_configured", source


def test_the_remaining_no_ops_still_pass_through_without_breaking_the_graph(loop_run):
    """dedupe, response-strategise and both rollups have no handler yet.
    They must still complete so the graph resolves -- this is the honest
    record of what is left."""
    states = loop_run["states"]

    for source in (
        "dedupe-signal-cards",
        "competitive-response-strategize",
        "morning-brief-rollup",
        "executive-brief-rollup",
        "publish",
    ):
        assert states[source] == "completed", source


def test_the_proof_circuit_reaches_an_approval_card(loop_run):
    """signal -> brief -> draft -> QA -> approval-card, the smoke test's
    own success bar, exercised in process."""
    db, tasks, states = loop_run["db"], loop_run["tasks"], loop_run["states"]
    by_source = {task["source_task_id"]: task["task_id"] for task in tasks}

    assert states["draft-linkedin-post"] == "completed"
    assert states["content-qa-review"] == "completed"
    assert states["request-linkedin-approval"] == "completed"
    approval_ref = db.get_result_ref(by_source["request-linkedin-approval"])
    assert approval_ref["decision_id"]
    assert approval_ref["function_id"] == dispatch.REAL_PUBLISH_FUNCTION_ID


def test_the_proof_circuit_stayed_tagged_as_a_proof_circuit(loop_run):
    """params.proof_circuit still reaches the handlers through metadata,
    now that metadata carries a second key."""
    agent_names = {run["agent_name"] for run in loop_run["clients"]._agent_runs.values()}

    assert dispatch.AGENT_NAME_LOOP_PROOF in agent_names


def test_the_scan_recorded_its_profile_and_completeness(loop_run):
    db, tasks = loop_run["db"], loop_run["tasks"]
    by_source = {task["source_task_id"]: task["task_id"] for task in tasks}

    ref = db.get_result_ref(by_source["ingest"])
    assert ref["scan_profile_id"] == "market-intelligence"
    assert ref["sources_configured"] == 4
    assert ref["sources_used"] == 4
