"""AC-15 — an agent (not only a human via console click-through) can
programmatically query task statuses + linked spans for a task_ref, AND
the REAL gatekeeper approval_inbox decision status for the run's
request-approval stage, via a plain script (curl/httpx, no browser/
interactive Entra session).
"""

from __future__ import annotations

from typing import Any

import httpx


def test_runs_endpoint_returns_machine_readable_state(
    live_daily_loop_run: dict[str, Any], orchestrator_base_url: str
) -> None:
    by_source = live_daily_loop_run["by_source"]
    target = by_source["request-linkedin-approval"]

    # Plain httpx.get, no browser, no Entra session -- exactly AC-15's
    # "agent-native" requirement.
    runs_url = f"{orchestrator_base_url.rstrip('/')}/runs/{target['task_id']}"
    response = httpx.get(runs_url, timeout=10.0)
    assert response.status_code == 200
    body = response.json()

    assert body["task_ref"] == target["task_id"]
    assert body["stage_count"] >= 3
    task_types = {s["task_type"] for s in body["stages"]}
    assert {"draft-content", "qa-review", "request-approval"} <= task_types

    # AC-15's core point: approval_decision_status is distinct from (and
    # not merely a copy of) request-approval's own always-COMPLETED-once-
    # issued task state.
    approval_stage = next(s for s in body["stages"] if s["task_type"] == "request-approval")
    assert approval_stage["state"] == "completed"  # the TASK is done...
    decision = body.get("approval_decision_status")
    assert decision is not None, "expected a real approval_decision_status for this run"
    assert decision.get("status") in ("pending", "approved", "rejected", "unknown"), (
        f"unexpected approval decision status shape: {decision}"
    )


def test_unknown_task_ref_returns_404(orchestrator_base_url: str) -> None:
    response = httpx.get(
        f"{orchestrator_base_url.rstrip('/')}/runs/00000000-0000-0000-0000-000000000000",
        timeout=10.0,
    )
    assert response.status_code == 404
