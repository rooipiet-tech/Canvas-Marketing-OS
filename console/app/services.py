"""Console service layer — single source of truth for both the HTML and
JSON route handlers (see console/app/rendering.py's render_or_json).

SCOPE-005: every function here reads from a VaultApiClient / GatekeeperClient
/ AppInsightsClient — never Postgres, never SQL.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date as date_type
from decimal import Decimal
from typing import Any

from app.clients.app_insights_client import AppInsightsClient
from app.clients.base import VaultApiClient
from app.clients.gatekeeper_base import GatekeeperClient
from app.models import ApprovalRow, AssetRow, KillSwitchState, SpanRow, TaskRow

# --- task queue / trace timeline (CONSOLE-001, step 8/9) -------------------


async def get_task_queue(vault_client: VaultApiClient) -> list[TaskRow]:
    runs = await vault_client.list_agent_runs()
    return [
        TaskRow(
            id=str(run["id"]),
            agent_name=run["agent_name"],
            status=run["status"],
            campaign=run.get("campaign"),
            started_at=run.get("started_at"),
        )
        for run in runs
    ]


def get_trace_timeline(app_insights_client: AppInsightsClient, task_ref: str) -> list[SpanRow]:
    spans = app_insights_client.get_trace_spans(task_ref)
    return [SpanRow(**span) for span in spans]


# --- approval inbox (CONSOLE-002, step 10) ----------------------------------


async def get_approval_inbox(gatekeeper_client: GatekeeperClient) -> list[ApprovalRow]:
    entries = await gatekeeper_client.list_approval_inbox()
    return [
        ApprovalRow(
            id=str(entry["id"]),
            function_id=entry["function_id"],
            action_class=entry["action_class"],
            level=entry["level"],
            preview_title=entry["preview_title"],
            status=entry["status"],
            decided_by=entry.get("decided_by"),
            decided_at=entry.get("decided_at"),
        )
        for entry in entries
    ]


# --- Vault search (CONSOLE-003, step 11) ------------------------------------

_TAXONOMY_FILTER_FIELDS = (
    "vertical",
    "function_id",
    "campaign",
    "evidence_grade",
    "consent_status",
    "retention_class",
)

_OBJECT_TYPE_LISTERS = {
    "assets": lambda client: client.list_assets(),
    "opportunity_cards": lambda client: client.list_opportunity_cards(),
    "campaigns": lambda client: client.list_campaigns(),
    "signals": lambda client: client.list_signals(),
}


async def search_vault(
    vault_client: VaultApiClient,
    *,
    object_type: str = "assets",
    vertical: str | None = None,
    function_id: str | None = None,
    campaign: str | None = None,
    evidence_grade: str | None = None,
    consent_status: str | None = None,
    retention_class: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch the full list from the matching vault-api list endpoint, THEN
    filter in Python by any supplied taxonomy kwarg.

    Interim client-side mechanism: as observed, the real vault-api
    contract's list endpoints support only Limit/Offset — no server-side
    taxonomy filter parameter (see INTEG-001). Revisit once/if s2's
    contract gains filter params.
    """
    lister = _OBJECT_TYPE_LISTERS.get(object_type, _OBJECT_TYPE_LISTERS["assets"])
    records = await lister(vault_client)

    filters = {
        "vertical": vertical,
        "function_id": function_id,
        "campaign": campaign,
        "evidence_grade": evidence_grade,
        "consent_status": consent_status,
        "retention_class": retention_class,
    }
    active_filters = {key: value for key, value in filters.items() if value is not None}

    def matches(record: dict[str, Any]) -> bool:
        return all(record.get(key) == value for key, value in active_filters.items())

    return [record for record in records if matches(record)]


def to_asset_rows(records: list[dict[str, Any]]) -> list[AssetRow]:
    return [
        AssetRow(
            id=str(r["id"]),
            asset_type=r.get("asset_type", ""),
            approval_state=r.get("approval_state", ""),
            vertical=r["vertical"],
            function_id=r["function_id"],
            campaign=r.get("campaign"),
            evidence_grade=r["evidence_grade"],
            consent_status=r["consent_status"],
            retention_class=r["retention_class"],
        )
        for r in records
    ]


# --- cost ledger (CONSOLE-004, GOAL-002, step 12) ---------------------------


def _incurred_date(cost: dict[str, Any]) -> str:
    raw = cost["incurred_at"]
    if isinstance(raw, str):
        return raw[:10]
    if isinstance(raw, date_type):
        return raw.isoformat()
    return str(raw)[:10]


async def get_cost_ledger(
    vault_client: VaultApiClient,
    *,
    group_by: str = "function",
    date: str | None = None,
) -> list[dict[str, Any]]:
    """Group cost fixtures by function_id or by UTC day.

    ALL summation/grouping arithmetic uses decimal.Decimal (never float),
    over the same canonical Decimal amounts VaultApiMock stores internally
    — this is what makes GOAL-002's byte-for-byte comparison against an
    independently-computed harness aggregation achievable by construction.
    """
    costs = await vault_client.list_costs()
    if date is not None:
        costs = [c for c in costs if _incurred_date(c) == date]

    totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for cost in costs:
        amount = cost["amount"]
        amount_decimal = amount if isinstance(amount, Decimal) else Decimal(str(amount))
        if group_by == "day":
            key = _incurred_date(cost)
        else:
            key = cost["function_id"]
        totals[key] += amount_decimal

    return [{"group_key": key, "total": total} for key, total in sorted(totals.items())]


# --- kill switch (CONSOLE-005, GOAL-004, step 13) ---------------------------


async def get_kill_switch_state(gatekeeper_client: GatekeeperClient) -> KillSwitchState:
    state = await gatekeeper_client.get_kill_switch_state()
    last_audit_entry = await gatekeeper_client.get_last_audit_entry()
    return KillSwitchState(**state, last_audit_entry=last_audit_entry)


async def toggle_kill_switch(
    gatekeeper_client: GatekeeperClient,
    *,
    active: bool,
    reason: str,
    operator: str,
) -> KillSwitchState:
    state = await gatekeeper_client.toggle_kill_switch(
        active=active, reason=reason, operator=operator
    )
    return KillSwitchState(**state)
