"""The smoke must say what the twelve scans did, not only that the
proof circuit passed.

F-SMOKE-SCAN-COVERAGE.

WHAT THE SMOKE COULD NOT TELL YOU. deploy-loop-e2e-smoke polls the
request-linkedin-approval lineage: ingest -> score -> draft -> QA ->
approval, seven stages. The eleven fan-out scanners hang off `ingest` in
PARALLEL with that chain, so not one of them is in the lineage. The smoke
passed identically whether all eleven scanned, all eleven reported "not
configured", or a sourced one dead-lettered on its retrieval floor.

That was harmless while every scanner was sourceless and the answer never
varied. It stopped being harmless on 2 Sep 2026, when competitor-discovery
and fabric-ecosystem were given five real source urls that had never been
probed. "Did those urls return anything" became a live question the deploy
could not answer: run 6 of deploy-pipeline went green end to end and its
smoke log contained nothing at all about either profile. The only way to
find out was ca-orchestrator's own logs, which the workflow dumps ONLY on
failure -- so the information existed and was unreachable on exactly the
runs where someone would think to look.

Each scan's result_ref already holds the answer. The report walks the
predicted batch and prints one line per profile.

WHY THESE TESTS EXIST IN THIS SHAPE. The report is the kind of code that
rots silently: nothing depends on its output, so a wrong line reads the
same as a right one. So the line CONTENT is asserted, not just its
presence -- and separately, that adding the report did not make the smoke
gate on it.
"""

from __future__ import annotations

from typing import Any

import pytest
from orchestrator import loop_e2e_smoke as smoke

MARKER = smoke.SCAN_REPORT_MARKER


def _task(source_task_id: str, profile_id: str | None, task_id: str = "") -> dict[str, Any]:
    return {
        "task_id": task_id or f"{source_task_id}-uuid",
        "task_type": source_task_id,
        "source_task_id": source_task_id,
        "params": {"profile_id": profile_id} if profile_id else {},
    }


def _stage(task_id: str, state: str, result_ref: dict | None = None) -> dict[str, Any]:
    return {"task_id": task_id, "state": state, "result_ref": result_ref}


# ---------------------------------------------------------------------
# Which tasks the report covers
# ---------------------------------------------------------------------


def test_scanning_tasks_are_the_ones_carrying_a_profile():
    batch = [
        _task("ingest", "market-intelligence"),
        _task("score", None),
        _task("competitor-discovery-scan", "competitor-discovery"),
        _task("qa", None),
    ]

    assert [t["source_task_id"] for t in smoke.scanning_tasks(batch)] == [
        "ingest",
        "competitor-discovery-scan",
    ]


def test_the_real_daily_loop_yields_all_twelve_scans():
    """Against the shipped loop, not a fixture.

    Keyed on params.profile_id rather than a hardcoded task-type list, so
    a twelfth scanner added to daily-signal-loop.yaml is picked up with no
    edit to the smoke. This asserts the current shape: `ingest` (the
    market-intelligence scan) plus the eleven fan-out scanners.
    """
    heartbeat = smoke.build_heartbeat()
    targets = smoke.scanning_tasks(smoke.predict_batch(heartbeat))

    assert len(targets) == 12
    sources = {t["source_task_id"] for t in targets}
    assert "ingest" in sources
    assert "competitor-discovery-scan" in sources
    assert "fabric-ecosystem-scout" in sources
    # Every one carries a real profile id -- a blank would render "?" in
    # the report and tell nobody anything.
    assert all((t["params"] or {}).get("profile_id") for t in targets)


def test_predicting_the_batch_still_finds_the_proof_circuit_target():
    """The refactor split predict_batch from predict_target_task_id; the
    target must still resolve out of the same batch."""
    heartbeat = smoke.build_heartbeat()
    batch = smoke.predict_batch(heartbeat)

    target = smoke.predict_target_task_id(batch)

    by_source = {t["source_task_id"]: t["task_id"] for t in batch}
    assert target == by_source[smoke.TARGET_SOURCE_TASK_ID]


# ---------------------------------------------------------------------
# What each line says
# ---------------------------------------------------------------------


def test_a_sourceless_profile_says_so_and_names_the_reason():
    task = _task("vertical-scan-manufacturing", "vertical-manufacturing")
    stage = _stage(
        task["task_id"],
        "completed",
        {"status": "not_configured", "profile_id": "vertical-manufacturing"},
    )

    line = smoke.describe_scan(task, stage)

    assert "vertical-manufacturing" in line
    assert "not_configured" in line
    assert "scan-profiles.yaml" in line


def test_a_scanned_profile_reports_cards_and_the_sources_behind_them():
    """Both numbers, deliberately.

    cards=0 with sources_used=3 is a quiet market. cards=0 with
    sources_used=1 of 3 is a sourcing problem wearing the same face. A
    report showing only the card count cannot tell them apart, which is
    the confusion the content floor exists to end.
    """
    task = _task("competitor-discovery-scan", "competitor-discovery")
    stage = _stage(
        task["task_id"],
        "completed",
        {
            "status": "scanned",
            "card_count": 4,
            "sources_configured": 3,
            "sources_used": 2,
            "repeat_count": 1,
        },
    )

    line = smoke.describe_scan(task, stage)

    assert "competitor-discovery" in line
    assert "cards=4" in line
    assert "sources_used=2/3" in line
    assert "repeats=1" in line


def test_a_dead_lettered_scan_points_at_where_to_look():
    """The shape a promoted-but-dead source takes.

    All three urls thin or unreachable fails the retrieval floor
    (F-INGEST-CONTENT-FLOOR), which dead-letters the scanner task. There
    is no result_ref in that case, so the line carries the task_id
    instead -- the one thing that makes ca-orchestrator's logs greppable.
    """
    task = _task("fabric-ecosystem-scout", "fabric-ecosystem", task_id="abc-123")
    stage = _stage("abc-123", "dead_lettered", None)

    line = smoke.describe_scan(task, stage)

    assert "fabric-ecosystem" in line
    assert "dead_lettered" in line
    assert "abc-123" in line


def test_a_task_with_no_run_state_is_reported_not_omitted():
    """A silently missing line is the failure mode this report replaces."""
    line = smoke.describe_scan(_task("competitor-change-monitor", "competitor-change"), None)

    assert "competitor-change" in line
    assert "no run state" in line


def test_every_line_carries_the_grep_marker():
    task = _task("ingest", "market-intelligence")
    for stage in (
        None,
        _stage(task["task_id"], "completed", {"status": "not_configured"}),
        _stage(task["task_id"], "completed", {"card_count": 3, "sources_used": 4}),
        _stage(task["task_id"], "dead_lettered", None),
        _stage(task["task_id"], "completed", {"something": "else"}),
    ):
        assert smoke.describe_scan(task, stage).startswith(MARKER)


# ---------------------------------------------------------------------
# The report as a whole
# ---------------------------------------------------------------------


@pytest.fixture()
def no_sleep(monkeypatch):
    monkeypatch.setattr(smoke.time, "sleep", lambda _s: None)


def test_the_report_covers_every_profile_once(monkeypatch, no_sleep, capsys):
    batch = [
        _task("ingest", "market-intelligence", task_id="t1"),
        _task("score", None, task_id="t2"),
        _task("competitor-discovery-scan", "competitor-discovery", task_id="t3"),
    ]
    stages = {
        "t1": _stage("t1", "completed", {"card_count": 3, "sources_used": 4,
                                         "sources_configured": 4, "repeat_count": 0}),
        "t3": _stage("t3", "completed", {"status": "not_configured"}),
    }
    monkeypatch.setattr(smoke, "fetch_stage", lambda _url, tid: stages.get(tid))

    lines = smoke.report_scan_coverage("https://orch.invalid/runs", batch)

    assert len(lines) == 2
    assert "market-intelligence" in lines[0] and "cards=3" in lines[0]
    assert "competitor-discovery" in lines[1] and "not_configured" in lines[1]
    # And it actually printed -- the log is the whole point.
    assert MARKER in capsys.readouterr().out


def test_a_scan_still_in_flight_is_waited_for_then_reported(monkeypatch, no_sleep):
    """Scanners run parallel to the proof circuit and usually settle
    first; when one has not, it is given a bounded wait and then reported
    in whatever state it is -- never waited out indefinitely."""
    batch = [_task("competitor-discovery-scan", "competitor-discovery", task_id="t1")]
    calls = {"n": 0}

    def _fetch(_url, _tid):
        calls["n"] += 1
        if calls["n"] < 3:
            return _stage("t1", "running", None)
        return _stage("t1", "completed", {"card_count": 2, "sources_used": 3,
                                          "sources_configured": 3, "repeat_count": 0})

    monkeypatch.setattr(smoke, "fetch_stage", _fetch)

    lines = smoke.report_scan_coverage("https://orch.invalid/runs", batch)

    assert "cards=2" in lines[0]
    assert calls["n"] == 3


def test_a_scan_that_never_settles_is_reported_not_hung(monkeypatch, no_sleep):
    batch = [_task("competitor-discovery-scan", "competitor-discovery", task_id="t1")]
    calls = {"n": 0}

    def _fetch(_url, _tid):
        calls["n"] += 1
        return _stage("t1", "running", None)

    monkeypatch.setattr(smoke, "fetch_stage", _fetch)

    lines = smoke.report_scan_coverage("https://orch.invalid/runs", batch)

    assert "running" in lines[0]
    # Bounded: one pass per attempt, and no more.
    assert calls["n"] == smoke.SCAN_SETTLE_ATTEMPTS


def test_the_report_never_raises_out_of_its_own_error_path(monkeypatch, no_sleep, capsys):
    """A diagnostic that fails the deploy it is diagnosing is worse than
    no diagnostic.

    This test caught a real overclaim: report_scan_coverage's docstring
    said "never raises" while the code propagated anything fetch_stage
    let through. The guarantee now exists rather than being asserted.
    """
    batch = [_task("competitor-discovery-scan", "competitor-discovery", task_id="t1")]
    monkeypatch.setattr(
        smoke, "fetch_stage", lambda _u, _t: (_ for _ in ()).throw(RuntimeError("boom"))
    )

    lines = smoke.report_scan_coverage("https://orch.invalid/runs", batch)

    assert len(lines) == 1
    assert "report failed" in lines[0]
    assert "boom" in lines[0]
    assert "smoke verdict unaffected" in lines[0]
    assert MARKER in capsys.readouterr().out


def test_fetch_stage_swallows_transport_errors(monkeypatch):
    """fetch_stage is where that guarantee actually lives: it returns
    None on any transport failure, so report_scan_coverage never sees an
    exception from a flaky read."""

    def _boom(*_a, **_kw):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(smoke.httpx, "get", _boom)

    assert smoke.fetch_stage("https://orch.invalid/runs", "t1") is None


def test_fetch_stage_picks_the_requested_task_out_of_its_lineage():
    """GET /runs/{task_ref} returns the task PLUS its ancestors, so the
    scanner's own stage has to be selected rather than assumed first."""

    class _Resp:
        status_code = 200

        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {
                "stages": [
                    {"task_id": "ingest-uuid", "state": "completed", "result_ref": {}},
                    {"task_id": "t1", "state": "dead_lettered", "result_ref": None},
                ]
            }

    import orchestrator.loop_e2e_smoke as module

    original = module.httpx.get
    module.httpx.get = lambda *_a, **_kw: _Resp()
    try:
        stage = smoke.fetch_stage("https://orch.invalid/runs", "t1")
    finally:
        module.httpx.get = original

    assert stage is not None
    assert stage["task_id"] == "t1"
    assert stage["state"] == "dead_lettered"


# ---------------------------------------------------------------------
# The report must not become a gate by accident
# ---------------------------------------------------------------------


def test_the_report_does_not_change_pass_fail():
    """evaluate_final_state is the smoke's verdict and knows nothing
    about scans. A future edit that makes a dead_lettered scanner fail
    the smoke is a deliberate decision -- it must not arrive by someone
    wiring the report into the verdict without noticing.
    """
    import inspect

    verdict_source = inspect.getsource(smoke.evaluate_final_state)

    assert "scan" not in verdict_source.lower()
    assert MARKER not in verdict_source
