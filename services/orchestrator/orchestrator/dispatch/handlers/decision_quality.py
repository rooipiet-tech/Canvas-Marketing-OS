from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from options_inbox.cards import build_card

from orchestrator.models import TaskEnvelope, TaskStateEnum, TransitionReason

from .. import clients, core
from ..core import _agent_name, _campaign_name
from ..scan_shared import _parse_iso_timestamp
from .source_lifecycle import LIFECYCLE_SIGNAL_LOOKBACK

# ---------------------------------------------------------------------
# Fn 126 -- Decision Quality Evaluator (Appendix D W1 + PR 6)
# ---------------------------------------------------------------------
#
# No model call, exactly like Fn 129's rule engine: every metric here is
# arithmetic over GET /decision-history rows (real approval_decisions
# joined with the producing card -- see that endpoint's own docstring,
# added by this PR because nothing exposed this data to orchestrator at
# all before now).
#
# SCOPE CUT, DOCUMENTED. prompt.md's "proposed_level_change" step needs a
# function's CURRENT autonomy level to compare against a promotion
# threshold -- but no ledger anywhere in this repo persists a level that
# actually changed (services/gatekeeper/policy/autonomy.yaml has no entry
# for 116/128/129 at all; policies/earn-in-rules.yaml's own `defaults` are
# just STARTING levels for a function with no line yet). Without that
# ledger, "promotion eligible" can only ever be evaluated against each
# function's unchanging default level, which is not what "current level"
# means. So this PR wires DEMOTION only (services/options_inbox/
# earn_in.evaluate_demotion), which needs no persisted state -- a
# demotion trigger firing is a real, meaningful fact about THIS window's
# decisions regardless of what level anything is nominally at. Promotion
# (evaluate_promotion) additionally needs gate_pass_rate/fabricated_
# proof_point_events/material_failures, none of which GET /decision-
# history carries (those are QA-verdict-level signals from a different
# data source entirely) -- wiring both the level ledger and that second
# data source is a further, separate follow-up.

FUNCTION_ID_126 = "126-decision-quality-evaluator"
DECISION_HISTORY_WINDOW_DAYS = 30
DECISION_QUALITY_SCORECARD_SIGNAL_TYPE = "decision_quality_scorecard"
LEVEL_REVIEW_PASS_MARKER_TYPE = "decision_quality_level_review_pass_marker"
LEVEL_REVIEW_PASS_MIN_DAYS = 30

# option_cards.produced_by_function -> earn-in-rules.yaml action_class,
# for the functions that actually BUILD OptionCards (Fn 117 routes them,
# it never appears as a produced_by_function value). Extend this map as
# each new card-producing function lands.
FUNCTION_ACTION_CLASS: dict[int, str] = {
    116: "compose_options",
    128: "mine",
    129: "configure",
}

# prompt.md task step 6's own routing table.
REJECTION_CODE_TARGET_FUNCTION: dict[str, int] = {
    "options_not_distinct": 102,
    "too_generic": 102,
    "off_brand_voice": 114,
    "claim_unsupported": 48,
}


def _decision_quality_metrics_for(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Recommendation Hit Rate, Rejection-All Rate, timeout share,
    distinctness/evidence coverage (read directly from each row's own
    embedded `card`, never re-derived from a model), and the rejection-
    code histogram -- prompt.md's own metric list, all computed from real
    decision-history rows."""
    decisions = len(rows)
    chosen = [row for row in rows if row.get("outcome") == "chosen"]
    recommendation_hit_rate = (
        sum(1 for row in chosen if row.get("was_recommended")) / len(chosen) if chosen else 0.0
    )
    rejected_all = sum(1 for row in rows if row.get("outcome") == "rejected_all")
    rejection_all_rate = rejected_all / decisions if decisions else 0.0
    timed_out = sum(1 for row in rows if row.get("outcome") == "timeout_default")
    timeout_share = timed_out / decisions if decisions else 0.0

    rejection_codes: dict[str, int] = {}
    for row in rows:
        code = row.get("rejection_code")
        if code:
            rejection_codes[code] = rejection_codes.get(code, 0) + 1

    total_options = 0
    distinct_options = 0
    evidenced_options = 0
    for row in rows:
        for option in (row.get("card") or {}).get("options") or []:
            total_options += 1
            if option.get("distinctness_axis"):
                distinct_options += 1
            if option.get("evidence_refs"):
                evidenced_options += 1

    return {
        "decisions": decisions,
        "recommendation_hit_rate": round(recommendation_hit_rate, 4),
        "rejection_all_rate": round(rejection_all_rate, 4),
        "timeout_share": round(timeout_share, 4),
        "distinctness_pass_rate": (
            round(distinct_options / total_options, 4) if total_options else None
        ),
        "evidence_coverage": (
            round(evidenced_options / total_options, 4) if total_options else None
        ),
        "rejection_codes": rejection_codes,
    }


def decision_quality_evaluate_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    """Fn 126 nightly scoring (prompt.md task step 1). Groups GET
    /decision-history's trailing-30-day rows by produced_by_function,
    scores each, and routes the actionable rejection codes to their
    target function as an improvement brief (task step's own routing
    table)."""
    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=DECISION_HISTORY_WINDOW_DAYS)).isoformat()
    with clients.build_vault_client() as vault:
        campaign_id = vault.get_or_create_campaign(
            _campaign_name(envelope), function_id=FUNCTION_ID_126
        )
        rows = vault.list_decision_history(since=since, limit=2000)

        by_function: dict[int, list[dict[str, Any]]] = {}
        for row in rows:
            by_function.setdefault(int(row["produced_by_function"]), []).append(row)

        functions_out: list[dict[str, Any]] = []
        improvement_briefs: list[dict[str, Any]] = []
        for function_id, function_rows in sorted(by_function.items()):
            metrics = _decision_quality_metrics_for(function_rows)
            entry = {
                "function_id": function_id,
                "decisions": metrics["decisions"],
                "recommendation_hit_rate": metrics["recommendation_hit_rate"],
                "rejection_all_rate": metrics["rejection_all_rate"],
                "timeout_share": metrics["timeout_share"],
                "rejection_codes": metrics["rejection_codes"],
            }
            if metrics["distinctness_pass_rate"] is not None:
                entry["distinctness_pass_rate"] = metrics["distinctness_pass_rate"]
            if metrics["evidence_coverage"] is not None:
                entry["evidence_coverage"] = metrics["evidence_coverage"]
            functions_out.append(entry)

            for code, count in sorted(metrics["rejection_codes"].items()):
                target = REJECTION_CODE_TARGET_FUNCTION.get(code)
                if target is None:
                    continue
                improvement_briefs.append(
                    {
                        "target_function": target,
                        "rejection_code": code,
                        "count": count,
                        "brief": (
                            f"Fn {function_id}: {count} '{code}' rejection(s) in the "
                            f"trailing {DECISION_HISTORY_WINDOW_DAYS} days."
                        ),
                    }
                )

        output = {
            "period": f"{since}/{now.isoformat()}",
            "functions": functions_out,
            "improvement_briefs": improvement_briefs,
        }
        core._validate_function_output(FUNCTION_ID_126, output)

        agent_run = vault.create_agent_run_idempotent(
            task_id=task_id,
            db=db,
            agent_name=_agent_name("decision-quality-evaluator", envelope),
            campaign_id=campaign_id,
            function_id=FUNCTION_ID_126,
            status="succeeded",
            input_payload={
                "window_days": DECISION_HISTORY_WINDOW_DAYS,
                "decision_count": len(rows),
            },
            output_payload=output,
        )
        signal = vault.create_signal(
            source=f"function-{FUNCTION_ID_126}",
            signal_type=DECISION_QUALITY_SCORECARD_SIGNAL_TYPE,
            payload=output,
            campaign_id=campaign_id,
            function_id=FUNCTION_ID_126,
        )

    db.set_result_ref(
        task_id,
        {
            "status": "scored",
            "vault_signal_id": signal["id"],
            "agent_run_id": agent_run["id"],
            "function_count": len(functions_out),
            "improvement_brief_count": len(improvement_briefs),
            "campaign_id": campaign_id,
        },
    )
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)


def decision_quality_level_review_monthly_handler(
    task_id: str, envelope: TaskEnvelope, db: Any
) -> None:
    """Fn 126 monthly review (prompt.md task step 2), DEMOTION ONLY -- see
    the module-section docstring above for why promotion needs a level
    ledger and a QA-verdict data source this PR does not add. Self-gating
    exactly like source_retire_handler/web_reach_allowlist_monthly_
    review_handler: runs inside the daily loop, no-ops as `not_due` unless
    LEVEL_REVIEW_PASS_MIN_DAYS have passed since its own last real pass."""
    from options_inbox import earn_in

    now = datetime.now(timezone.utc)
    with clients.build_vault_client() as vault:
        campaign_id = vault.get_or_create_campaign(
            _campaign_name(envelope), function_id=FUNCTION_ID_126
        )
        recent = vault.list_signals(limit=LIFECYCLE_SIGNAL_LOOKBACK)
        last_pass_at: datetime | None = None
        for row in recent:
            if row.get("signal_type") != LEVEL_REVIEW_PASS_MARKER_TYPE:
                continue
            candidate_time = _parse_iso_timestamp(row.get("received_at"))
            if candidate_time and (last_pass_at is None or candidate_time > last_pass_at):
                last_pass_at = candidate_time
        if last_pass_at and (now - last_pass_at).days < LEVEL_REVIEW_PASS_MIN_DAYS:
            db.set_result_ref(
                task_id,
                {
                    "status": "not_due",
                    "next_due_in_days": LEVEL_REVIEW_PASS_MIN_DAYS - (now - last_pass_at).days,
                    "campaign_id": campaign_id,
                },
            )
            db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
            db.advance_dependents(task_id)
            return

        rules = earn_in.load_rules()
        since = (now - timedelta(days=DECISION_HISTORY_WINDOW_DAYS)).isoformat()
        rows = vault.list_decision_history(since=since, limit=2000)
        by_function: dict[int, list[dict[str, Any]]] = {}
        for row in rows:
            by_function.setdefault(int(row["produced_by_function"]), []).append(row)

        demoted: list[dict[str, Any]] = []
        for function_id, action_class in sorted(FUNCTION_ACTION_CLASS.items()):
            function_rows = by_function.get(function_id, [])
            metrics = _decision_quality_metrics_for(function_rows)
            signals = {
                "recommendation_hit_rate": metrics["recommendation_hit_rate"],
                "rejection_all_rate": metrics["rejection_all_rate"],
                "decision_count": metrics["decisions"],
                "run_count": metrics["decisions"],
            }
            fired = earn_in.evaluate_demotion(signals=signals, rules=rules)
            if not fired:
                continue

            agent_run = vault.create_agent_run_idempotent(
                task_id=task_id,
                db=db,
                agent_name=_agent_name("decision-quality-evaluator", envelope),
                campaign_id=campaign_id,
                function_id=FUNCTION_ID_126,
                status="succeeded",
                input_payload={"function_id": function_id, "action_class": action_class},
                output_payload={
                    "fired": [{"trigger": d.trigger, "action": d.action} for d in fired]
                },
            )
            most_severe = fired[0]
            evidence = (
                f"Fn {function_id} ({action_class}) over the trailing "
                f"{DECISION_HISTORY_WINDOW_DAYS} days: recommendation_hit_rate="
                f"{metrics['recommendation_hit_rate']}, rejection_all_rate="
                f"{metrics['rejection_all_rate']}, decisions={metrics['decisions']}. "
                f"Triggered: {most_severe.trigger} -> {most_severe.action}."
            )
            card = build_card(
                kind="system.autonomy_level_change",
                level=0,  # overridden to non_negotiable/realtime by build_card itself
                title=f"Fn {function_id}: demote per {most_severe.trigger}"[:120],
                decision_question=f"Apply {most_severe.action} to function {function_id}?",
                options=[
                    {
                        "option_id": "A",
                        "label": "Apply",
                        "summary": f"{most_severe.action} for function {function_id}."[:400],
                        "payload_ref": f"vault://agent-run/{agent_run['id']}",
                        "evidence_refs": [
                            {
                                "source_type": "vault_asset",
                                "ref": f"vault://agent-run/{agent_run['id']}",
                                "quote": evidence[:300],
                                "authority": "primary",
                            }
                        ],
                        "predicted_outcome": (
                            "Function's autonomy level is reduced per the fired trigger."
                        ),
                        "risks": [],
                    },
                    {
                        "option_id": "B",
                        "label": "Hold",
                        "summary": f"Do not change function {function_id}'s level yet."[:400],
                        "payload_ref": f"vault://agent-run/{agent_run['id']}",
                        "evidence_refs": [
                            {
                                "source_type": "vault_asset",
                                "ref": f"vault://agent-run/{agent_run['id']}",
                                "quote": evidence[:300],
                                "authority": "primary",
                            }
                        ],
                        "predicted_outcome": "No change; re-evaluated next monthly pass.",
                        "risks": [],
                    },
                ],
                recommended="A",
                evidence_refs=[
                    {
                        "source_type": "vault_asset",
                        "ref": f"vault://agent-run/{agent_run['id']}",
                        "authority": "primary",
                    }
                ],
                produced_by={"function_id": 126, "prompt_version": "0.1.0"},
                register_rows=["H14", "H26"],
                rationale=evidence,
                lineage={"agent_run_id": agent_run["id"], "source_task_id": task_id},
            )
            created = vault.create_option_card(
                {
                    "card_id": card["card_id"],
                    "kind": card["kind"],
                    "autonomy_level": card["autonomy_level"],
                    "risk_tier": card["risk_tier"],
                    "agent_run_id": agent_run["id"],
                    "produced_by_function": 126,
                    "card": card,
                    "created_at": card["created_at"],
                    "expires_at": card["expires_at"],
                }
            )
            demoted.append(
                {
                    "function_id": function_id,
                    "card_id": created["card_id"],
                    "trigger": most_severe.trigger,
                }
            )

        vault.create_signal(
            source=f"function-{FUNCTION_ID_126}",
            signal_type=LEVEL_REVIEW_PASS_MARKER_TYPE,
            payload={"demoted_count": len(demoted)},
            campaign_id=campaign_id,
            function_id=FUNCTION_ID_126,
        )

    db.set_result_ref(
        task_id,
        {
            "status": "review_pass_complete",
            "demoted": demoted,
            "campaign_id": campaign_id,
        },
    )
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)


