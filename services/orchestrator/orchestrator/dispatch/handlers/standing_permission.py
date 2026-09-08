from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from options_inbox.cards import build_card, load_matrix

from orchestrator.clients.vault_client_ext import VaultClientExt
from orchestrator.models import TaskEnvelope, TaskStateEnum, TransitionReason

from .. import clients, core
from ..core import _agent_name, _campaign_name, _now_iso, _validate_function_input
from .source_lifecycle import LIFECYCLE_SIGNAL_LOOKBACK

# ---------------------------------------------------------------------
# Fn 118 -- Standing-Permission Learner (Appendix D PR 10)
# ---------------------------------------------------------------------
#
# No model call -- deterministic grouping/thresholding over GET /decision-
# history, exactly like Fn 126's own scorecard and Fn 129's own rule
# engine. "Proposes only" (prompt.md's own words): route_digest_handler
# already documents, in its own comment, that its `permissions` argument
# to route() is hardcoded `[]` because "Fn 118's seed loop... is what
# will ever populate real ones" -- this PR is that seed loop's proposal
# half. Materializing a GRANTED system.standing_permission card into
# route_digest_handler's real permissions source is a further, separate
# step this PR does not add: it needs reconstructing a full
# StandingPermission document from whichever option a ratifier chose,
# which the lightweight OptionCard option shape (label/summary/
# payload_ref) does not carry losslessly -- a real design question of its
# own, not a one-line follow-up.
#
# scope.channels is deliberately never populated: approval_decisions.
# channel (contracts/approval-decision.schema.json) records HOW a
# decision arrived (teams_card/console_inbox/digest_email/system), not
# WHICH platform a card's content targets (contracts/standing-
# permission.schema.json's channels enum is linkedin_company/
# linkedin_personal/newsletter/website/facebook/x) -- two same-named-
# sounding but disjoint concepts. Inventing a mapping between them would
# be a fabricated classification this data cannot honestly support.

FUNCTION_ID_118 = "118-standing-permission-learner"
STANDING_PERMISSION_PROPOSAL_SIGNAL_TYPE = "standing_permission_proposal"
STANDING_PERMISSION_WINDOW_DAYS = 90
STANDING_PERMISSION_MIN_DECISIONS = 20
STANDING_PERMISSION_MIN_HIT_RATE = 0.85
# SP-001..006 are hand-seeded (policies/standing-permissions-seed.yaml);
# Fn 118's own proposals start past the highest of those.
STANDING_PERMISSION_SEEDED_MAX = 6


def _next_standing_permission_id(vault: VaultClientExt) -> str:
    highest = STANDING_PERMISSION_SEEDED_MAX
    for row in vault.list_signals(limit=LIFECYCLE_SIGNAL_LOOKBACK):
        if row.get("signal_type") != STANDING_PERMISSION_PROPOSAL_SIGNAL_TYPE:
            continue
        permission_id = str((row.get("payload") or {}).get("permission_id", ""))
        if permission_id.startswith("SP-"):
            try:
                highest = max(highest, int(permission_id[3:]))
            except ValueError:
                pass
    return f"SP-{highest + 1:03d}"


def standing_permission_learner_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    """Fn 118 (prompt.md task, weekly). Groups the trailing 90 days of
    GET /decision-history by (kind, produced_by_function); any group with
    >= STANDING_PERMISSION_MIN_DECISIONS decisions, Recommendation Hit
    Rate >= STANDING_PERMISSION_MIN_HIT_RATE and zero rejected_all drafts
    a system.standing_permission card (hard limit: never for a
    non_negotiable kind, checked before anything else -- 'the validator
    will reject it, but do not make it try')."""
    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=STANDING_PERMISSION_WINDOW_DAYS)).isoformat()
    non_negotiable_kinds = set(load_matrix()["non_negotiable_kinds"])

    with clients.build_vault_client() as vault:
        campaign_id = vault.get_or_create_campaign(
            _campaign_name(envelope), function_id=FUNCTION_ID_118
        )
        rows = vault.list_decision_history(since=since, limit=2000)

        groups: dict[tuple[str, int], list[dict[str, Any]]] = {}
        for row in rows:
            kind = row.get("kind")
            if not kind or kind in non_negotiable_kinds:
                continue
            groups.setdefault((kind, int(row["produced_by_function"])), []).append(row)

        proposals: list[dict[str, Any]] = []
        for (kind, function_id), group_rows in sorted(groups.items()):
            decisions = len(group_rows)
            if decisions < STANDING_PERMISSION_MIN_DECISIONS:
                continue
            chosen = [row for row in group_rows if row.get("outcome") == "chosen"]
            hit_rate = (
                sum(1 for row in chosen if row.get("was_recommended")) / len(chosen)
                if chosen
                else 0.0
            )
            rejected_all = sum(1 for row in group_rows if row.get("outcome") == "rejected_all")
            if hit_rate < STANDING_PERMISSION_MIN_HIT_RATE or rejected_all > 0:
                continue

            permission_id = _next_standing_permission_id(vault)
            review_by_full = (now + timedelta(days=90)).date().isoformat()
            review_by_narrow = (now + timedelta(days=30)).date().isoformat()
            evidence = {
                "decisions_observed": decisions,
                "recommendation_hit_rate": round(hit_rate, 4),
                "rejections_in_scope": rejected_all,
                "note": (
                    f"Trailing {STANDING_PERMISSION_WINDOW_DAYS} days: {decisions} decisions on "
                    f"kind={kind!r} produced by function {function_id}, hit rate "
                    f"{round(hit_rate, 2)}, zero rejected_all."
                ),
            }
            draft_permission_full = {
                "permission_id": permission_id,
                "scope": {"card_kinds": [kind], "functions": [function_id]},
                "rule": {
                    "effect": "auto_approve_recommended",
                    "condition": "True",  # scope alone already restricts kind+function
                    "hard_exclusions": sorted(non_negotiable_kinds),
                },
                "granted_by": "not_yet_granted",
                "granted_at": _now_iso(),
                "review_by": review_by_full,
                "status": "proposed",
                "evidence": evidence,
                "suspend_if": {
                    "guardrail_breach_kinds": sorted(non_negotiable_kinds),
                    "hit_rate_below": 0.40,
                },
            }
            draft_permission_narrow = {
                **draft_permission_full,
                "review_by": review_by_narrow,
            }

            agent_run = vault.create_agent_run_idempotent(
                task_id=task_id,
                db=db,
                agent_name=_agent_name("standing-permission-learner", envelope),
                campaign_id=campaign_id,
                function_id=FUNCTION_ID_118,
                status="running",
                input_payload={"kind": kind, "function_id": function_id, "decisions": decisions},
            )
            input_payload = {
                "window_days": STANDING_PERMISSION_WINDOW_DAYS,
                "min_decisions": STANDING_PERMISSION_MIN_DECISIONS,
                "min_hit_rate": STANDING_PERMISSION_MIN_HIT_RATE,
            }
            _validate_function_input(FUNCTION_ID_118, input_payload)

            proposal_batch = vault.create_signal(
                source=f"function-{FUNCTION_ID_118}",
                signal_type=STANDING_PERMISSION_PROPOSAL_SIGNAL_TYPE,
                payload={
                    "permission_id": permission_id,
                    "draft_full": draft_permission_full,
                    "draft_narrow": draft_permission_narrow,
                },
                campaign_id=campaign_id,
                function_id=FUNCTION_ID_118,
            )

            evidence_text = evidence["note"]
            options = [
                {
                    "option_id": "A",
                    "label": f"Grant {permission_id}",
                    "summary": (
                        f"Auto-approve the recommended option for {kind} from function "
                        f"{function_id}, review in 90 days."
                    )[:400],
                    "payload_ref": f"vault://signal/{proposal_batch['id']}#draft_full",
                    "evidence_refs": [
                        {
                            "source_type": "vault_asset",
                            "ref": f"vault://signal/{proposal_batch['id']}",
                            "quote": evidence_text[:300],
                            "authority": "primary",
                        }
                    ],
                    "predicted_outcome": (
                        "This kind/function pair stops reaching the daily digest."
                    ),
                    "risks": [
                        "A future regression in this pair's quality is caught only at "
                        "review_by or a guardrail breach."
                    ],
                    "distinctness_axis": "full 90-day grant",
                },
                {
                    "option_id": "B",
                    "label": f"Grant {permission_id} (narrower)",
                    "summary": (
                        "Same scope, 30-day review instead of 90 -- re-evaluate sooner."
                    )[:400],
                    "payload_ref": f"vault://signal/{proposal_batch['id']}#draft_narrow",
                    "evidence_refs": [
                        {
                            "source_type": "vault_asset",
                            "ref": f"vault://signal/{proposal_batch['id']}",
                            "quote": evidence_text[:300],
                            "authority": "primary",
                        }
                    ],
                    "predicted_outcome": "Same digest reduction, reviewed again in 30 days.",
                    "risks": [],
                    "distinctness_axis": "narrower 30-day grant",
                },
                {
                    "option_id": "C",
                    "label": "Do not grant",
                    "summary": "Keep reviewing every card in this group individually."[:400],
                    "payload_ref": f"vault://signal/{proposal_batch['id']}",
                    "evidence_refs": [
                        {
                            "source_type": "vault_asset",
                            "ref": f"vault://signal/{proposal_batch['id']}",
                            "quote": evidence_text[:300],
                            "authority": "primary",
                        }
                    ],
                    "predicted_outcome": "No change; this group keeps consuming digest budget.",
                    "risks": [],
                    "distinctness_axis": "no grant, status quo",
                },
            ]
            card = build_card(
                kind="system.standing_permission",
                level=0,  # overridden to non_negotiable/realtime by build_card itself
                title=f"Grant {permission_id}: {kind} / fn {function_id}"[:120],
                decision_question=(
                    f"Grant a standing permission for {kind} from function {function_id}?"
                ),
                options=options,
                recommended="A",
                evidence_refs=[
                    {
                        "source_type": "vault_asset",
                        "ref": f"vault://signal/{proposal_batch['id']}",
                        "authority": "primary",
                    }
                ],
                produced_by={"function_id": 118, "prompt_version": "0.1.0"},
                register_rows=["H23", "H28", "H29"],
                rationale=evidence_text,
                lineage={"agent_run_id": agent_run["id"], "source_task_id": task_id},
            )
            created = vault.create_option_card(
                {
                    "card_id": card["card_id"],
                    "kind": card["kind"],
                    "autonomy_level": card["autonomy_level"],
                    "risk_tier": card["risk_tier"],
                    "agent_run_id": agent_run["id"],
                    "produced_by_function": 118,
                    "card": card,
                    "created_at": card["created_at"],
                    "expires_at": card["expires_at"],
                }
            )
            vault.update_agent_run(
                agent_run["id"],
                status="succeeded",
                output_payload={"permission_id": permission_id, "card_id": created["card_id"]},
                completed_at=_now_iso(),
            )
            proposals.append(
                {
                    "permission_id": permission_id,
                    "kind": kind,
                    "function_id": function_id,
                    "decisions": decisions,
                    "recommendation_hit_rate": round(hit_rate, 4),
                    "card_id": created["card_id"],
                }
            )

        output = {"proposals": proposals}
        core._validate_function_output(FUNCTION_ID_118, output)

    db.set_result_ref(
        task_id,
        {
            "status": "proposed" if proposals else "no_qualifying_groups",
            "proposals": proposals,
            "campaign_id": campaign_id,
        },
    )
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)


