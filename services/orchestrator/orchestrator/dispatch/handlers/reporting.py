from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from orchestrator.logging_config import log_event
from orchestrator.models import TaskEnvelope, TaskStateEnum, TransitionReason

from .. import clients
from ..core import _campaign_name, logger

# =======================================================================
# PROCESS 9 -- report on cost and performance.
# =======================================================================
#
# infra/modules/scheduling/month-end-reporting-trigger.bicep has been
# firing a `month-end-reporting` heartbeat on the last day of every month
# since it shipped, and NO loop file carries that loop_id. The trigger's
# own header says so in as many words. worker.handle_heartbeat_message
# logs `heartbeat_unknown_loop` and returns an empty list, so every
# month-end since has produced one warning line and nothing else.
#
# The report is DETERMINISTIC -- no model call, no function package.
# Every figure is read back from what the other processes recorded:
# costs metered on each model call, the nightly KPI rollups, the
# attribution outcomes, the publish sweep's own result_refs. A report of
# numbers should not be a model's paraphrase of numbers, and one that
# derives figures nobody else recorded is one nobody can check.
#
# WHAT MAKES IT HONEST. A month-end report that prints "0 posts
# published" while the publisher sits in dry-run is not a report, it is a
# misdiagnosis: it says the marketing failed when the truth is nobody
# turned it on. Every section therefore states not just its number but
# whether that number means anything yet, and the caveats are DERIVED
# from the data rather than hardcoded -- so they disappear on their own
# the moment the underlying gap closes, instead of aging into a lie.

REPORT_FUNCTION_ID = "report.month_end"


def _fmt_money(amount: str | None) -> str:
    try:
        return f"${Decimal(str(amount)):.2f}"
    except (InvalidOperation, TypeError):
        return "$0.00"


def _month_window(today: date) -> tuple[date, date]:
    """The month `today` falls in, as [start, next month start).

    The trigger fires on the last day of the month, so the final hours of
    that day are not yet in the data. Reporting the containing month --
    rather than the previous one -- is what makes the report about the
    month a reader has just lived through; the missing tail is one
    evening, and naming the window in the report itself is what keeps
    that visible rather than implied."""
    start = today.replace(day=1)
    end = date(start.year + 1, 1, 1) if start.month == 12 else date(start.year, start.month + 1, 1)
    return start, end


def _report_caveats(
    costs: dict[str, Any],
    kpis: dict[str, Any],
    attribution: dict[str, Any],
    publishes: dict[str, Any],
) -> list[str]:
    """What this month's numbers cannot yet be read as saying.

    Derived, never hardcoded: each line is emitted only while the
    condition that justifies it holds, so the caveat list shrinks by
    itself as the pipeline is wired up rather than needing an edit that
    someone will forget."""
    caveats: list[str] = []

    statuses = {row["status"]: row["count"] for row in publishes.get("by_status", [])}
    dry_run = statuses.get("published_dry_run", 0)
    live = publishes.get("total", 0) - dry_run
    if dry_run and not live:
        caveats.append(
            f"NOTHING WAS ACTUALLY POSTED. All {dry_run} publish(es) ran in dry-run "
            "(PUBLISHER_DRY_RUN defaults to true and is set nowhere in infra), so every "
            "engagement figure below is necessarily empty. This is a configuration "
            "state, not a marketing result."
        )

    quarantined = attribution.get("quarantined_total", 0)
    if quarantined:
        reasons = ", ".join(
            f"{row['rows']}x {row['reason']}" for row in attribution["quarantined_by_reason"]
        )
        caveats.append(
            f"{quarantined} ingested metric row(s) could not be attributed to any campaign "
            f"({reasons}). Performance figures cover only what did match."
        )
    if not attribution.get("registered_campaigns"):
        # Two different faults wear the same empty map, and telling a
        # reader the wrong one sends them to the wrong place. Registration
        # happens when an asset publishes, so with no publishes the map is
        # empty for the obvious reason; with publishes and still no map,
        # something is actually wrong.
        if publishes.get("total"):
            caveats.append(
                "No campaign is registered in analytics.utm_campaign_map even though "
                f"{publishes['total']} asset(s) published this month, so no metric can "
                "match one. Either those assets carried no campaign tag or the "
                "registration failed — see the publish sweep's own result_refs."
            )
        else:
            caveats.append(
                "No campaign is registered in analytics.utm_campaign_map, so no metric "
                "can match one. Registration happens when an asset publishes, and "
                "nothing published this month."
            )

    if not kpis.get("engagement"):
        caveats.append("No engagement data: no published post carried a matched campaign tag.")
    if not kpis.get("reliability"):
        caveats.append(
            "No publishing-reliability figure: nothing recorded a scheduled post this month."
        )
    if not costs.get("calls"):
        caveats.append("No model calls were metered this month — the loops did not run.")
    return caveats


def _render_month_end_report(
    window: tuple[date, date],
    costs: dict[str, Any],
    kpis: dict[str, Any],
    attribution: dict[str, Any],
    publishes: dict[str, Any],
) -> str:
    start, end = window
    lines = [
        f"# Month-end report — {start:%B %Y}",
        "",
        f"Covering {start:%Y-%m-%d} to {end:%Y-%m-%d} (exclusive). Generated on the "
        "last day of the month, so that day's final hours are not included.",
        "",
        "## Cost",
        "",
        f"Total: {_fmt_money(costs.get('total'))} across {costs.get('calls', 0)} metered "
        "model call(s).",
    ]
    for row in costs.get("by_provider", []):
        lines.append(f"  - {row['provider']}: {_fmt_money(row['amount'])} ({row['calls']} calls)")
    if costs.get("by_agent"):
        lines.append("")
        lines.append("By agent:")
        for row in costs["by_agent"]:
            lines.append(
                f"  - {row['agent_name']}: {_fmt_money(row['amount'])} ({row['calls']} calls)"
            )

    lines += ["", "## Delivery", ""]
    if publishes.get("total"):
        for row in publishes["by_status"]:
            lines.append(f"  - {row['status']}: {row['count']}")
    else:
        lines.append("  - Nothing was published this month.")

    lines += ["", "## Performance", ""]
    if kpis.get("engagement"):
        for row in kpis["engagement"]:
            lines.append(
                f"  - {row['source']}/{row['post_archetype']}: engagement rate "
                f"{row['engagement_rate']} over {row['posts']} post(s)"
            )
    else:
        lines.append("  - No engagement data for this month.")
    for row in kpis.get("reliability", []):
        lines.append(
            f"  - {row['channel']} publishing reliability: {row['published']} published "
            f"of {row['scheduled']} scheduled"
        )
    for row in kpis.get("cost_per_accepted_asset", []):
        lines.append(
            f"  - {row['agent_name']}: {_fmt_money(row['cost'])} across "
            f"{row['accepted_assets']} accepted asset(s)"
        )

    caveats = _report_caveats(costs, kpis, attribution, publishes)
    if caveats:
        lines += ["", "## Read this before the numbers above", ""]
        lines += [f"  - {caveat}" for caveat in caveats]
    return "\n".join(lines)


def report_month_end_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    """Assembles the month-end cost and performance report and files it.

    Filed as a Vault brief because that is where this system already puts
    a document a person is meant to read (draft_brief_handler does the
    same for the daily brief), and notified to Teams best-effort -- the
    notification no-ops without a webhook, and a report that exists in the
    Vault but was not announced is still a report, whereas one that
    dead-letters because Teams is unconfigured is not."""
    window = _month_window(date.today())
    start, end = window
    costs = db.month_costs(start, end)
    kpis = db.month_kpis(start, end)
    attribution = db.month_attribution(start, end)
    publishes = db.month_publishes(start, end)

    body = _render_month_end_report(window, costs, kpis, attribution, publishes)
    title = f"Month-end report — {start:%B %Y}"

    with clients.build_vault_client() as vault:
        campaign_id = vault.get_or_create_campaign(
            _campaign_name(envelope), function_id=REPORT_FUNCTION_ID
        )
        brief = vault.create_brief(
            title=title,
            body_text=body,
            campaign_id=campaign_id,
            function_id=REPORT_FUNCTION_ID,
        )

    from orchestrator import teams_notify

    notified = teams_notify.notify_brief_ready(
        title=title, brief_id=brief["id"], executive_brief_id=brief["id"]
    )

    caveat_count = len(_report_caveats(costs, kpis, attribution, publishes))
    log_event(
        logger,
        logging.INFO,
        "month_end_report_filed",
        month=f"{start:%Y-%m}",
        total_cost=costs.get("total"),
        published=publishes.get("total", 0),
        caveats=caveat_count,
        notified=notified,
    )
    db.set_result_ref(
        task_id,
        {
            "brief_id": brief["id"],
            "campaign_id": campaign_id,
            "month": f"{start:%Y-%m}",
            "total_cost": costs.get("total"),
            "metered_calls": costs.get("calls", 0),
            "published": publishes.get("total", 0),
            "quarantined": attribution.get("quarantined_total", 0),
            "caveats": caveat_count,
            "teams_notified": notified,
        },
    )
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)


