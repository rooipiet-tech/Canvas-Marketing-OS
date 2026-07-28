"""All read-only (GET) console routes.

Every route decorates the shared `app` object directly (`@app.get`, never
an APIRouter) — see app_instance.py's docstring for why that matters to
CONSOLE-005's grep-based verify. This file contains no mutating-verb route
decorator of any kind (CONSOLE-002's own verify greps specifically for
that pattern — deliberately not spelled out literally in this docstring,
since the literal string would itself be a false-positive match).
"""

from __future__ import annotations

from fastapi import Depends, Request

from app.app_instance import app, templates
from app.clients import (
    AppInsightsClient,
    GatekeeperClient,
    VaultApiClient,
    get_app_insights_client,
    get_gatekeeper_client,
    get_vault_client,
)
from app.rendering import render_or_json
from app.services import (
    get_approval_inbox,
    get_cost_ledger,
    get_kill_switch_state,
    get_task_queue,
    get_trace_timeline,
    search_vault,
    to_asset_rows,
)

# --- task queue / trace timeline (CONSOLE-001, AGENT-001) -------------------


@app.get("/tasks")
async def tasks_page(
    request: Request, vault_client: VaultApiClient = Depends(get_vault_client)
):
    rows = await get_task_queue(vault_client)
    data = {"rows": [row.model_dump() for row in rows]}
    return render_or_json(request, "tasks.html", data, templates)


@app.get("/tasks/{task_ref}/trace")
async def task_trace_page(
    request: Request,
    task_ref: str,
    app_insights_client: AppInsightsClient = Depends(get_app_insights_client),
):
    rows = get_trace_timeline(app_insights_client, task_ref)
    data = {"task_ref": task_ref, "rows": [row.model_dump() for row in rows]}
    return render_or_json(request, "trace.html", data, templates)


# --- approval inbox (CONSOLE-002) -------------------------------------------


@app.get("/approvals")
async def approvals_page(
    request: Request, gatekeeper_client: GatekeeperClient = Depends(get_gatekeeper_client)
):
    rows = await get_approval_inbox(gatekeeper_client)
    data = {"rows": [row.model_dump() for row in rows]}
    return render_or_json(request, "approvals.html", data, templates)


# --- Vault search (CONSOLE-003) ---------------------------------------------


@app.get("/vault-search")
async def vault_search_page(
    request: Request,
    object_type: str = "assets",
    vertical: str | None = None,
    function_id: str | None = None,
    campaign: str | None = None,
    evidence_grade: str | None = None,
    consent_status: str | None = None,
    retention_class: str | None = None,
    vault_client: VaultApiClient = Depends(get_vault_client),
):
    records = await search_vault(
        vault_client,
        object_type=object_type,
        vertical=vertical,
        function_id=function_id,
        campaign=campaign,
        evidence_grade=evidence_grade,
        consent_status=consent_status,
        retention_class=retention_class,
    )
    rows = to_asset_rows(records) if object_type == "assets" else records
    data = {
        "object_type": object_type,
        "rows": [r.model_dump() if hasattr(r, "model_dump") else r for r in rows],
        "filters": {
            "vertical": vertical,
            "function_id": function_id,
            "campaign": campaign,
            "evidence_grade": evidence_grade,
            "consent_status": consent_status,
            "retention_class": retention_class,
        },
    }
    return render_or_json(request, "vault_search.html", data, templates)


# --- cost ledger (CONSOLE-004, GOAL-002) ------------------------------------


@app.get("/costs")
async def costs_page(
    request: Request,
    group_by: str = "function",
    date: str | None = None,
    vault_client: VaultApiClient = Depends(get_vault_client),
):
    grouped = await get_cost_ledger(vault_client, group_by=group_by, date=date)
    data = {"group_by": group_by, "date": date, "rows": grouped}
    return render_or_json(request, "costs.html", data, templates)


# --- kill switch (CONSOLE-005) — read half only, POST lives in routes_write.py


@app.get("/kill-switch")
async def kill_switch_page(
    request: Request, gatekeeper_client: GatekeeperClient = Depends(get_gatekeeper_client)
):
    state = await get_kill_switch_state(gatekeeper_client)
    data = {"state": state.model_dump()}
    return render_or_json(request, "kill_switch.html", data, templates)
