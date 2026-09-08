from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

from orchestrator.dispatch_errors import DispatchError
from orchestrator.logging_config import log_event
from orchestrator.models import TaskEnvelope, TaskStateEnum, TransitionReason

from .. import clients
from ..core import _campaign_name, logger
from ..lineage import resolve_lineage_result
from .scanner import _ancestor_is_quiet, _complete_quiet_scan_noop
from .scoring import FUNCTION_ID_SIGNAL_SCORE


def _collect_rollup_inputs(task_id: str, db: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """The deduped cards and the response plan, read from this task's own
    two depends_on rows.

    morning-brief-rollup depends on BOTH dedupe-signal-cards and
    competitive-response-strategize, so lineage resolution would take
    whichever it reached first and lose the other."""
    current = db.get_task(task_id) or {}
    cards_ref: dict[str, Any] = {}
    plan_ref: dict[str, Any] = {}
    for row in db.get_tasks(current.get("depends_on") or []):
        ref = row.get("result_ref") or {}
        if row.get("task_type") == "dedupe-signal-cards":
            cards_ref = ref
        elif row.get("task_type") == "competitive-response-strategize":
            plan_ref = ref
    return cards_ref, plan_ref


def _coverage_line(cards_ref: dict[str, Any]) -> str:
    """One sentence of scanner coverage, or "" when every scanner is live.

    Stated in the brief rather than left in a log line because eleven
    dormant scanners currently COMPLETE -- honestly, with
    status=not_configured on their own result_ref, but green all the same.
    Anything asking "did the daily loop succeed?" sees success, and the
    fact that most of the market is unwatched lives only in JSONB nobody
    reads. Silence here is the failure mode, not a red loop.

    Says nothing when coverage is complete: a line that appears every day
    regardless is a line people stop seeing.
    """
    total = cards_ref.get("scanner_total") or 0
    dormant = cards_ref.get("dormant_count") or 0
    if not total or not dormant:
        return ""
    configured = cards_ref.get("configured_count", total - dormant)
    profiles = ", ".join(cards_ref.get("dormant_profiles") or [])
    detail = f" Dormant: {profiles}." if profiles else ""
    return (
        f"**Coverage: {configured} of {total} scanners configured; "
        f"{dormant} dormant, awaiting sources.**"
        f"{detail} A dormant scanner reads nothing -- it is not a quiet market."
    )


def _empty_cards_line(cards_ref: dict[str, Any]) -> str:
    """The no-cards line, saying WHICH kind of nothing this was.

    "Every scanner either found nothing or has no sources configured"
    conflated the two states a reader most needs told apart.
    """
    total = cards_ref.get("scanner_total") or 0
    dormant = cards_ref.get("dormant_count") or 0
    if total and dormant == total:
        return (
            f"- No cards, and none were possible: all {total} scanner(s) are dormant "
            "(no source urls configured). Nothing scanned the market today."
        )
    if dormant:
        return (
            f"- No cards. {total - dormant} configured scanner(s) found nothing; "
            f"the other {dormant} are dormant and did not look."
        )
    return "- No cards. Every configured scanner ran and found nothing."


def _render_intel_brief(
    cards_ref: dict[str, Any], plan_ref: dict[str, Any]
) -> tuple[str, str]:
    """The competitive-intelligence brief and its executive edition.

    Deterministic, and explicit about provenance: `seen by N scanners` is
    the one fact that only exists because eleven profiles were merged, so
    it leads each line. An empty morning says so plainly rather than
    rendering an empty section that reads like a formatting fault."""
    cards = cards_ref.get("cards") or []
    plan = plan_ref.get("response_plan") or []
    scanners = cards_ref.get("scanners_read", 0)
    removed = max(0, cards_ref.get("cards_in", 0) - cards_ref.get("cards_out", 0))
    coverage_line = _coverage_line(cards_ref)

    full = [
        "# Morning Brief — competitive intelligence",
        "",
        f"{len(cards)} distinct item(s) from {scanners} scanner(s); "
        f"{removed} duplicate(s) merged.",
    ]
    if coverage_line:
        full += ["", coverage_line]
    if plan_ref.get("summary"):
        full += ["", plan_ref["summary"]]

    full += ["", "## What the scanners found", ""]
    if cards:
        for card in cards:
            domain = urlparse(str(card.get("source_url", ""))).hostname or "unknown-source"
            seen = card.get("seen_by", 1)
            corroboration = f" — seen by {seen} scanners" if seen > 1 else ""
            full.append(
                f"- [{card.get('card_type', '?')}/{card.get('evidence_grade', '?')}] "
                f"{card.get('headline', '')} — {card.get('so_what', '')} "
                f"(source: {domain}{corroboration})"
            )
    else:
        full.append(_empty_cards_line(cards_ref))

    full += ["", "## Response plan", ""]
    if plan:
        for item in plan:
            full.append(
                f"- [{item.get('severity', '?')}] {item.get('headline', '')} — "
                f"{item.get('playbook_template', '')}"
            )
    elif plan_ref.get("status") == "no_cards":
        full.append("- No plan: the strategist had no cards to rank.")
    else:
        full.append("- No response plan was produced.")

    exec_lines = [
        "# Executive Edition — competitive intelligence",
        "",
        plan_ref.get("summary")
        or f"{len(cards)} item(s) from {scanners} scanner(s), no response plan.",
    ]
    if coverage_line:
        exec_lines += ["", coverage_line]
    exec_lines += [
        "",
        "## Most urgent",
        "",
    ]
    if plan:
        for item in plan[:3]:
            exec_lines.append(f"- [{item.get('severity', '?')}] {item.get('headline', '')}")
    else:
        for card in cards[:3]:
            exec_lines.append(f"- {card.get('headline', '')}")
    if not plan and not cards:
        exec_lines.append("- Nothing to report this morning.")
    return "\n".join(full), "\n".join(exec_lines)


def morning_brief_rollup_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    """Files the competitive-intelligence brief the eleven scanners feed.

    Distinct from draft-brief's "Morning Brief — {topic}", which is built
    from the ingest path and never sees a scanner card. Two briefs
    because there are two independent branches in this loop, and merging
    them would mean one waiting on the other for no reason; the titles
    say which is which."""
    cards_ref, plan_ref = _collect_rollup_inputs(task_id, db)
    full_body, executive_body = _render_intel_brief(cards_ref, plan_ref)

    with clients.build_vault_client() as vault:
        campaign_id = vault.get_or_create_campaign(
            _campaign_name(envelope), function_id=FUNCTION_ID_SIGNAL_SCORE
        )
        brief = vault.create_brief(
            title="Morning Brief — competitive intelligence",
            body_text=full_body,
            campaign_id=campaign_id,
            function_id=FUNCTION_ID_SIGNAL_SCORE,
        )

    db.set_result_ref(
        task_id,
        {
            "brief_id": brief["id"],
            "campaign_id": campaign_id,
            "card_count": len(cards_ref.get("cards") or []),
            "plan_count": len(plan_ref.get("response_plan") or []),
            "executive_body": executive_body,
        },
    )
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)


def executive_brief_rollup_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    """Files the executive edition rendered by the morning rollup.

    The body was produced upstream, from the same cards and plan, rather
    than re-derived here: two renderings of one morning that could
    disagree is exactly the kind of drift the rest of this work has been
    removing."""
    lineage = resolve_lineage_result(task_id, db)
    if lineage is None:
        raise DispatchError("executive-brief-rollup: no morning-brief-rollup ancestor")
    _ancestor_task, ancestor_ref = lineage
    body = ancestor_ref.get("executive_body")
    if not body:
        raise DispatchError("executive-brief-rollup: ancestor carries no executive_body")

    with clients.build_vault_client() as vault:
        campaign_id = ancestor_ref.get("campaign_id") or vault.get_or_create_campaign(
            _campaign_name(envelope), function_id=FUNCTION_ID_SIGNAL_SCORE
        )
        brief = vault.create_brief(
            title="Executive Edition — competitive intelligence",
            body_text=body,
            campaign_id=campaign_id,
            function_id=FUNCTION_ID_SIGNAL_SCORE,
        )

    db.set_result_ref(
        task_id,
        {
            "brief_id": brief["id"],
            "morning_brief_id": ancestor_ref.get("brief_id"),
            "campaign_id": campaign_id,
        },
    )
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)


def publish_brief_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    """Announces the daily brief, AFTER its QA gate has passed.

    F-BRIEF-ANNOUNCED-BEFORE-QA. draft_brief_handler called
    teams_notify.notify_brief_ready the moment it created the brief --
    before the `qa` task had run at all -- so a brief that then failed QA
    had already been sent to the team, and the block that followed was
    invisible to whoever had read it. This task depends on `qa`, and a
    failed qa never advances its dependents, so announcing here is the
    same notification moved to the point where it is true.

    The notification is best-effort: it no-ops without a webhook, and a
    brief that is in the Vault but unannounced is still a brief."""
    lineage = resolve_lineage_result(task_id, db)
    if lineage is None:
        raise DispatchError("publish-brief: no QA-gate ancestor carries a result_ref")
    _ancestor_task, ancestor_ref = lineage
    if _ancestor_is_quiet(ancestor_ref):
        # Nothing is announced to the team on a quiet day. A "here is
        # today's brief" notification for an empty brief is worse than
        # silence, and the WARNING is the operator-facing signal instead.
        _complete_quiet_scan_noop(
            task_id, db, stage="publish-brief", reason="upstream reported a quiet scan"
        )
        return
    brief_id = ancestor_ref.get("brief_id")
    if not brief_id:
        raise DispatchError("publish-brief: QA-gate ancestor result_ref carries no brief_id")

    from orchestrator import teams_notify

    notified = teams_notify.notify_brief_ready(
        title="Morning Brief",
        brief_id=brief_id,
        executive_brief_id=ancestor_ref.get("executive_brief_id") or brief_id,
    )
    log_event(
        logger,
        logging.INFO,
        "brief_published",
        task_id=task_id,
        brief_id=brief_id,
        notified=notified,
    )
    db.set_result_ref(
        task_id,
        {
            "brief_id": brief_id,
            "executive_brief_id": ancestor_ref.get("executive_brief_id"),
            "notified": notified,
        },
    )
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)


