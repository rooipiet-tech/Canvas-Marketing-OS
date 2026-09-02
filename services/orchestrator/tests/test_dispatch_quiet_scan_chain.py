"""A quiet scan must stand the brief chain down, not relocate its failure.

F-INGEST-QUIET-ZERO.

WHAT WENT WRONG. schema.json's `signals` carried minItems 1 while
prompt.md hard rule 9 forbids padding a batch up to a minimum. A day on
which every retrieved source was already captured left the model two
schema-legal moves and both were wrong: pad, or emit nothing and be
rejected. deploy-pipeline run 9 hit it -- 3 of 4 sources already
captured, `[] should be non-empty`, three retries, dead-lettered, and
roughly twenty descendants cascade-dead-lettered behind it.

Most of that cascade was collateral. The ELEVEN fan-out scanners depend
on `ingest` but never read its signals, so a quiet market-intelligence
scan was taking down eleven unrelated scans, plus dedupe and both
rollups. Only score -> draft -> qa -> publish genuinely had nothing to
do.

WHY minItems 0 ALONE IS NOT THE FIX. Three gates in a row turn an honest
zero into a failure, and relaxing one just moves the dead-letter to the
next:

    schema.json minItems 1        `[] should be non-empty`
    _assert_signal_domain_floor   "cite 0 distinct domain(s)"
    score_signals_handler         "carries no signals to score"

These tests pin all three, and pin that the eleven scanners keep running.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from orchestrator import dispatch
from tests.test_dispatch import FakeTaskDB, _envelope


@pytest.fixture()
def clients(monkeypatch):
    from tests.fakes import patch_dispatch_clients

    return patch_dispatch_clients(monkeypatch)


class _EmptyBatchGateway:
    """Returns a schema-valid batch carrying no signals."""

    def __enter__(self) -> "_EmptyBatchGateway":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        pass

    def complete(self, *, agent_run_id: str, model: str = "claude-haiku", **_kw: Any):
        import json

        return {
            "id": f"fake-{uuid.uuid4()}",
            "model": model,
            "content": json.dumps(
                {"topic": "market", "horizon_days": 30, "summary": "x" * 60, "signals": []}
            ),
            "usage": {"input_tokens": 10, "output_tokens": 10},
            "agent_run_id": agent_run_id,
        }


def _quiet_ingest(db: FakeTaskDB, monkeypatch) -> str:
    ingest_id = str(uuid.uuid4())
    db.seed(ingest_id, "ingest-signals")
    monkeypatch.setattr(dispatch, "build_gateway_client", lambda: _EmptyBatchGateway())
    dispatch.ingest_signals_handler(ingest_id, _envelope(ingest_id, "ingest-signals"), db)
    return ingest_id


def test_the_whole_brief_chain_stands_down_without_dead_lettering(clients, monkeypatch):
    """score -> draft -> qa -> publish all complete, none raise."""
    db = FakeTaskDB()
    ingest_id = _quiet_ingest(db, monkeypatch)
    assert db.get_result_ref(ingest_id)["status"] == dispatch.QUIET_SCAN_STATUS

    previous = ingest_id
    for task_type, handler in (
        ("score-signals", dispatch.score_signals_handler),
        ("draft-brief", dispatch.draft_brief_handler),
        ("qa-review", dispatch.qa_review_handler),
        ("publish-brief", dispatch.publish_brief_handler),
    ):
        task_id = str(uuid.uuid4())
        db.seed(task_id, task_type, depends_on=[previous])
        handler(task_id, _envelope(task_id, task_type), db)

        assert db.get_task(task_id)["state"] == "completed", f"{task_type} did not complete"
        ref = db.get_result_ref(task_id)
        assert ref["status"] == dispatch.QUIET_SCAN_STATUS, f"{task_type} lost the marker"
        assert ref["stage"] == task_type
        previous = task_id

        if task_type == "score-signals":
            # Pins WHICH branch stood score-signals down. It has a second,
            # deliberate exit -- the `if not ranked` fallback for a batch
            # written before the marker existed -- and that fallback makes
            # the two paths behaviourally identical, so removing the
            # ancestor check is invisible unless the reason is asserted.
            # Verified: without this line, deleting the ancestor check
            # leaves every test in this file passing.
            assert ref["reason"] == "ingest reported a quiet scan", (
                "score-signals fell through to the empty-batch fallback instead of "
                "standing down on the propagated marker"
            )


def test_the_marker_propagates_rather_than_each_stage_rediscovering_it(clients, monkeypatch):
    """draft-brief stands down on score's marker alone.

    It never reads the signal batch, so the chain cannot be broken by a
    stage that happens not to look at the Vault.
    """
    db = FakeTaskDB()
    score_id = str(uuid.uuid4())
    draft_id = str(uuid.uuid4())
    db.seed(score_id, "score-signals")
    db.seed(draft_id, "draft-brief", depends_on=[score_id])
    db.set_result_ref(score_id, {"status": dispatch.QUIET_SCAN_STATUS, "stage": "score-signals"})

    dispatch.draft_brief_handler(draft_id, _envelope(draft_id, "draft-brief"), db)

    assert db.get_task(draft_id)["state"] == "completed"
    assert db.get_result_ref(draft_id)["status"] == dispatch.QUIET_SCAN_STATUS


def test_an_ordinary_scan_still_drives_a_real_brief(clients):
    """The both-directions half.

    A quiet-day path that also swallowed ordinary days would be a far
    worse bug than the one being fixed -- the loop would go silently
    green while publishing nothing, which is the failure mode L-0081's
    sibling incident and F-SMOKE-SCAN-COVERAGE both exist to catch.
    """
    db = FakeTaskDB()
    ingest_id = str(uuid.uuid4())
    draft_id = str(uuid.uuid4())
    db.seed(ingest_id, "ingest-signals")
    db.seed(draft_id, "draft-brief", depends_on=[ingest_id])

    dispatch.ingest_signals_handler(ingest_id, _envelope(ingest_id, "ingest-signals"), db)
    assert db.get_result_ref(ingest_id).get("status") != dispatch.QUIET_SCAN_STATUS

    dispatch.draft_brief_handler(draft_id, _envelope(draft_id, "draft-brief"), db)
    ref = db.get_result_ref(draft_id)
    assert ref.get("status") != dispatch.QUIET_SCAN_STATUS
    assert ref["brief_id"], "an ordinary scan must still produce a real brief"


def test_a_scanner_is_not_stood_down_by_a_quiet_ingest(clients, monkeypatch):
    """The eleven fan-out scanners depend on ingest but never read it.

    Standing them down would reproduce the cascade this change exists to
    end, just quietly instead of as a dead-letter.
    """
    db = FakeTaskDB()
    ingest_id = _quiet_ingest(db, monkeypatch)

    advanced = db.advance_dependents(ingest_id)
    scan_id = str(uuid.uuid4())
    db.seed(scan_id, "competitor-discovery-scan", depends_on=[ingest_id])

    # The marker is addressed to the brief chain; nothing about it may
    # make a scanner skip its own retrieval.
    assert dispatch._ancestor_is_quiet(db.get_result_ref(ingest_id)) is True
    assert "competitor-discovery-scan" not in {
        db.get_task(t)["task_type"] for t in advanced if db.get_task(t)
    }
    assert db.get_task(scan_id)["state"] != "completed"
