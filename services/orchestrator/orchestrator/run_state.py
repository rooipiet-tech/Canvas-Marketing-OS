"""GET /runs/{task_ref} — agent-native run-state endpoint (plan step 17;
AC-15).

Lets a plain script (curl/httpx, no browser/interactive Entra session)
ask "what happened for this run": every stage's task status (walking
depends_on lineage backward from `task_ref`, the same generalised walk
dispatch.resolve_lineage_result already does forward-looking for content
resolution), span presence (best-effort, via App Insights KQL when
configured -- console/app/clients/app_insights_client.py's own query
shape, degrading to "not_checked" rather than failing the whole response
when unconfigured/unreachable), and the REAL gatekeeper approval_inbox
decision status for the run's request-approval stage -- distinct from
that task's own always-COMPLETED-once-issued task state (AC-15's core
point: request-approval's OWN task status says nothing about whether a
human has actually approved/rejected yet).
"""

from __future__ import annotations

import logging
from typing import Any

from orchestrator.clients.gatekeeper_client import GatekeeperClient, resolve_gatekeeper_base_url
from orchestrator.logging_config import get_logger, log_event, sanitize_exception_text

logger = get_logger("run_state")

MAX_LINEAGE_HOPS = 20


def _collect_lineage(
    task_ref: str, db: Any, *, max_hops: int = MAX_LINEAGE_HOPS
) -> list[dict[str, Any]]:
    """BFS backward through depends_on from `task_ref`, collecting the
    task itself plus every transitive ancestor -- "every stage" of the
    run task_ref belongs to."""
    root = db.get_task(task_ref)
    if root is None:
        return []

    collected: dict[str, dict[str, Any]] = {task_ref: root}
    frontier = list(root.get("depends_on") or [])
    for _ in range(max_hops):
        if not frontier:
            break
        to_fetch = [tid for tid in frontier if tid not in collected]
        if not to_fetch:
            break
        rows = db.get_tasks(to_fetch)
        next_frontier: list[str] = []
        for row in rows:
            collected[row["task_id"]] = row
            for dep in row.get("depends_on") or []:
                if dep not in collected:
                    next_frontier.append(dep)
        frontier = next_frontier
    return list(collected.values())


def _span_presence(task_ids: list[str]) -> str:
    """Best-effort App Insights span-presence check. Never raises; a
    misconfigured/unreachable App Insights is reported as 'not_checked',
    not a 500 for the whole run-state response."""
    import os

    workspace_id = os.environ.get("APPLICATIONINSIGHTS_WORKSPACE_ID")
    if not workspace_id:
        return "not_checked"
    try:
        from azure.identity import DefaultAzureCredential
        from azure.monitor.query import LogsQueryClient

        client = LogsQueryClient(DefaultAzureCredential())
        task_ref_list = ", ".join(repr(t) for t in task_ids)
        query = (
            "union traces, dependencies, requests\n"
            f"| where tostring(customDimensions.task_ref) in ({task_ref_list})\n"
            "| count"
        )
        from datetime import timedelta

        response = client.query_workspace(workspace_id, query, timespan=timedelta(hours=24))
        for table in response.tables:
            for row in table.rows:
                if row and int(row[0]) > 0:
                    return "present"
        return "absent"
    except Exception as exc:  # noqa: BLE001 - best-effort only
        log_event(
            logger,
            logging.WARNING,
            "span_presence_check_failed",
            error=sanitize_exception_text(exc),
        )
        return "not_checked"


def _approval_decision_status(stages: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Finds the request-approval stage (if any) and queries Gatekeeper's
    REAL approval_inbox decision status -- distinct from that task's own
    (always-COMPLETED-once-issued) task state. Returns None if this run
    has no request-approval stage at all."""
    approval_task = next((s for s in stages if s.get("task_type") == "request-approval"), None)
    if approval_task is None:
        return None

    result_ref = approval_task.get("result_ref") or {}
    agent_run_id = result_ref.get("agent_run_id")
    function_id = result_ref.get("function_id")
    content_hash = result_ref.get("content_hash")
    if not agent_run_id or not function_id:
        return {"status": "unknown", "reason": "request-approval task has no result_ref yet"}

    try:
        base_url = resolve_gatekeeper_base_url()
        with GatekeeperClient(base_url=base_url) as gatekeeper:
            decision = gatekeeper.get_approval_status(
                agent_run_id=agent_run_id, function_id=function_id, content_hash=content_hash
            )
        return {
            "status": decision.get("status"),
            "decided_by": decision.get("decided_by"),
            "decided_at": decision.get("decided_at"),
        }
    except Exception as exc:  # noqa: BLE001 - a Gatekeeper outage must not break the whole response
        log_event(
            logger,
            logging.WARNING,
            "approval_status_lookup_failed",
            error=sanitize_exception_text(exc),
        )
        return {"status": "unknown", "reason": "approval-status lookup failed"}


def build_run_state(task_ref: str, db: Any) -> dict[str, Any] | None:
    """Returns the full run-state payload for `task_ref`, or None if
    task_ref is not a known task_id (caller returns 404)."""
    lineage = _collect_lineage(task_ref, db)
    if not lineage:
        return None

    stages = [
        {
            "task_id": task["task_id"],
            "task_type": task["task_type"],
            "state": task["state"],
            "result_ref": task.get("result_ref"),
        }
        for task in lineage
    ]
    task_ids = [s["task_id"] for s in stages]

    return {
        "task_ref": task_ref,
        "stage_count": len(stages),
        "stages": stages,
        "span_presence": _span_presence(task_ids),
        "approval_decision_status": _approval_decision_status(stages),
    }
