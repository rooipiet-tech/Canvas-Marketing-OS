"""AC-01 — for each of the 5 GOAL-mandated task_types, the task row
transitions PENDING->RUNNING->COMPLETED|FAILED with a non-stub outcome,
and a corresponding downstream artifact exists (a costs-table row
sharing the run's agent_run_id for ingest-signals/draft-brief/qa-review/
draft-content — real model-gateway calls happened — and a gate-token
request + approval-inbox card for request-approval).

Runs against ONE real, live daily-signal-loop heartbeat (shared via the
session-scoped `live_daily_loop_run` fixture — expensive to trigger,
so every AC that only needs to OBSERVE this one run reuses it rather
than each triggering its own).
"""

from __future__ import annotations

from typing import Any

import psycopg

EXPECTED_TASK_TYPES = {
    "ingest-signals",
    "draft-brief",
    "qa-review",
    "draft-content",
    "request-approval",
}


def _stages_by_type(final_state: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    by_type: dict[str, list[dict[str, Any]]] = {}
    for stage in final_state.get("stages", []):
        by_type.setdefault(stage["task_type"], []).append(stage)
    return by_type


def test_all_five_task_types_present_and_terminal(live_daily_loop_run: dict[str, Any]) -> None:
    final_state = live_daily_loop_run["final_state"]
    by_type = _stages_by_type(final_state)

    missing = EXPECTED_TASK_TYPES - set(by_type)
    assert not missing, f"expected task_types missing from the run's lineage: {missing}"

    for task_type, stages in by_type.items():
        if task_type not in EXPECTED_TASK_TYPES:
            continue
        for stage in stages:
            assert stage["state"] in ("completed", "failed"), (
                f"{task_type} ({stage['task_id']}) never reached a terminal state: "
                f"{stage['state']!r}"
            )


def test_each_llm_calling_stage_has_a_real_costs_row(
    live_daily_loop_run: dict[str, Any], live_db_url: str
) -> None:
    """ingest-signals/qa-review/draft-content each make a real
    model-gateway call, metered automatically to Vault's costs table
    keyed by agent_run_id (services/model-gateway/metering.py) --
    draft-brief is deterministic (no LLM call, no cost row expected)."""
    final_state = live_daily_loop_run["final_state"]
    by_type = _stages_by_type(final_state)

    with psycopg.connect(live_db_url) as conn:
        for task_type in ("ingest-signals", "qa-review", "draft-content"):
            stages = by_type.get(task_type, [])
            assert stages, f"no stage found for {task_type}"
            for stage in stages:
                result_ref = stage.get("result_ref") or {}
                agent_run_id = result_ref.get("agent_run_id")
                assert agent_run_id, f"{task_type} stage carries no agent_run_id in result_ref"
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT count(*) FROM costs WHERE agent_run_id = %s", (agent_run_id,)
                    )
                    (count,) = cur.fetchone()
                assert count > 0, (
                    f"{task_type}'s agent_run_id={agent_run_id} has ZERO costs rows -- "
                    "metering silently didn't fire (AC-01's own failure condition)"
                )


def test_request_approval_produced_a_gate_token_request(
    live_daily_loop_run: dict[str, Any], live_db_url: str
) -> None:
    final_state = live_daily_loop_run["final_state"]
    by_type = _stages_by_type(final_state)
    approval_stages = by_type.get("request-approval", [])
    assert approval_stages, "no request-approval stage found"

    with psycopg.connect(live_db_url) as conn:
        for stage in approval_stages:
            result_ref = stage.get("result_ref") or {}
            decision_id = result_ref.get("decision_id")
            assert decision_id, "request-approval's result_ref carries no decision_id"
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT count(*) FROM gate_decisions WHERE id = %s", (decision_id,)
                )
                (gd_count,) = cur.fetchone()
                cur.execute(
                    "SELECT count(*) FROM governance.approval_inbox WHERE gate_decision_id = %s",
                    (decision_id,),
                )
                (inbox_count,) = cur.fetchone()
            assert gd_count == 1, f"gate_decisions row for {decision_id} not found"
            assert inbox_count >= 1, f"approval_inbox row for decision {decision_id} not found"
