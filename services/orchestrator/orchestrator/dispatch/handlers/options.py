from __future__ import annotations

import base64
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from options_inbox.cards import build_card
from options_inbox.policy import route
from telemetry_lib import set_span_attribute

from orchestrator import brand_rules
from orchestrator.clients.gatekeeper_client import GatekeeperClient
from orchestrator.clients.gateway_client import OrchestratorGatewayClient
from orchestrator.clients.vault_client_ext import VaultClientExt
from orchestrator.dispatch_errors import DispatchError
from orchestrator.logging_config import log_event
from orchestrator.models import TaskEnvelope, TaskStateEnum, TransitionReason
from orchestrator.telemetry_wiring import emit_task_span

from .. import clients, core
from ..completion import _complete_and_meter
from ..core import (
    FUNCTION_ID_02,
    FUNCTION_ID_48_FACT_CHECK,
    FUNCTION_ID_116,
    FUNCTION_ID_117,
    _agent_name,
    _campaign_name,
    _now_iso,
    _parse_json_content,
    _read_prompt,
    _validate_function_input,
    logger,
)
from ..lineage import resolve_lineage_result
from ..qa_common import _resolve_verdict, _review_channel, _reviewable_draft_text

# ---------------------------------------------------------------------
# options-approval-loop.yaml -- Appendix D PR 5 (Fn 116 / Fn 117)
# ---------------------------------------------------------------------
# The ratification model's first live wiring: thursday-compose-options-
# {draft} (Fn 116, compose_options_handler) wraps each Wednesday draft
# into an OptionCard with two model-written alternates, and friday-
# route-digest (Fn 117, route_digest_handler) ranks/budgets/times-out
# the week's pending cards and posts the Teams digest -- see
# loops/options-approval-loop.yaml.
#
# Deliberate scope narrowing, both documented at their own call site
# rather than silently applied:
#   * legal_triage (function 124) is not run -- its package is still
#     status: scaffold, with no schema.json/tools.yaml to call it
#     against. Its own completion-plan row (App D PR 10-13) comes after
#     this one.
#   * Per-option QA here is single-shot, not the retry-loop machinery
#     _single_draft_qa_review uses for the single-draft weekly model. A
#     losing option is simply dropped (build_card only needs >=2 of the
#     3 candidates); the redundancy the retry loop exists to protect is
#     already provided by composing 3 candidates in the first place.
#   * build_card's evidence_resolver is not supplied -- verifying an
#     EvidenceRef against a real corpus atom needs the corpus atom index
#     Fn 113/114 will build (App D PR 8/9); until then this mirrors
#     every other drafting handler in this file, none of which resolve
#     evidence beyond schema shape either.
#   * Standing permissions passed to route() are always [] -- Fn 118's
#     seed loop (App D PR 10-13) is what will ever populate real ones;
#     route()  degrades correctly with none (every card just proceeds
#     straight to ranking/budget, per policy.py's own routing order).


def _option_evidence_refs(
    proof_points: list[dict[str, Any]], vault_asset_id: str
) -> list[dict[str, Any]]:
    """contracts/option-card.schema.json requires >=1 evidence_refs entry
    per card and per option. The week's research brief already carried
    {claim, source} proof points this far (F-FACT-CHECK-BLIND's own
    lineage carry) -- reused here as the evidence citation every
    composed option shares, since all three (the original draft and its
    two alternates) are drawn from the same approved proof list by
    construction (Fn 116's prompt.md: "Keep every claim inside the
    approved proof list"). Falls back to the original Vault asset itself
    when a draft carried no proof_points (e.g. a case study, whose own
    brief shape differs) rather than emitting an empty list the schema
    would reject."""
    if proof_points:
        return [
            {
                "source_type": "web_source",
                "ref": point.get("source") or "unknown",
                "quote": (point.get("claim") or "")[:300],
                "authority": "primary",
            }
            for point in proof_points
        ]
    return [{"source_type": "vault_asset", "ref": vault_asset_id, "authority": "primary"}]


def _run_option_qa(
    *,
    task_id: str,
    db: Any,
    vault: VaultClientExt,
    gateway: OrchestratorGatewayClient,
    envelope: TaskEnvelope,
    campaign_id: str,
    channel: str,
    text: str,
    proof_points: list[dict[str, Any]],
    option_label: str,
) -> tuple[bool, list[str]]:
    """Single-shot brand_steward (Fn 02) + fact_check (Fn 48) QA for one
    composed option. See the module-section docstring above for why this
    is single-shot rather than _single_draft_qa_review's retry loop.

    content_class="public_source_content" (F-WEEKLY-LOOP-DRAFT-PUBLIC-
    SOURCE, 7 Aug 2026, heartbeat round 20, Pieter's explicit ruling via
    AskUserQuestion: "Extend the exemption"). Not a new ruling -- this
    calls the identical Fn 02 / Fn 48 pair _single_draft_qa_review
    already calls under that exact ruling, over text drawn from the same
    weekly research brief (which legitimately names executives, clients
    and case-study subjects), and it is what REVIEWS a composed option
    before it can ever reach a card -- the same "explicitly reviewed
    before approval" property the ruling was granted for. See
    tests/test_public_source_content_allowlist.py's SIGNED_OFF entry."""
    permission_check = clients.load_permission_check()
    violations: list[str] = []

    brand_agent_run = vault.create_agent_run_idempotent(
        task_id=task_id,
        db=db,
        agent_name=_agent_name("brand-steward-qa", envelope),
        campaign_id=campaign_id,
        function_id=FUNCTION_ID_02,
        status="running",
        input_payload={"channel": channel, "review_kind": "brand_steward", "option": option_label},
    )
    brand_payload = {"draft_text": text, "client_references": [], "channel": channel}
    _validate_function_input(FUNCTION_ID_02, brand_payload)
    brand_response, _brand_cost = _complete_and_meter(
        gateway,
        vault,
        model="claude-sonnet",
        system_prompt=_read_prompt("02-brand-steward-qa"),
        user_content=json.dumps(brand_payload),
        agent_run_id=brand_agent_run["id"],
        content_class="public_source_content",
    )
    brand_verdict = _parse_json_content(brand_response["content"])
    brand_pass, brand_violations = _resolve_verdict(FUNCTION_ID_02, brand_verdict)
    violations.extend(brand_violations)
    vault.update_agent_run(
        brand_agent_run["id"],
        status="succeeded" if brand_pass else "failed",
        output_payload=brand_verdict,
        completed_at=_now_iso(),
    )

    fact_agent_run = vault.create_agent_run_idempotent(
        task_id=task_id,
        db=db,
        agent_name=_agent_name("fact-check-verdict", envelope),
        campaign_id=campaign_id,
        function_id=FUNCTION_ID_48_FACT_CHECK,
        status="running",
        input_payload={"channel": channel, "review_kind": "fact_check", "option": option_label},
    )
    fact_payload = {
        "draft_text": text,
        "client_references": [],
        "channel": channel,
        "proof_points": proof_points,
    }
    _validate_function_input(FUNCTION_ID_48_FACT_CHECK, fact_payload)
    fact_response, _fact_cost = _complete_and_meter(
        gateway,
        vault,
        model="claude-sonnet",
        system_prompt=_read_prompt("48-fact-check-verdict"),
        user_content=json.dumps(fact_payload),
        agent_run_id=fact_agent_run["id"],
        content_class="public_source_content",
    )
    fact_verdict = _parse_json_content(fact_response["content"])
    fact_pass, fact_violations = _resolve_verdict(FUNCTION_ID_48_FACT_CHECK, fact_verdict)
    violations.extend(fact_violations)
    vault.update_agent_run(
        fact_agent_run["id"],
        status="succeeded" if fact_pass else "failed",
        output_payload=fact_verdict,
        completed_at=_now_iso(),
    )

    uncleared = permission_check.find_uncleared_in_text(text)
    if uncleared and permission_check.VIOLATION_CODE not in violations:
        violations.append(permission_check.VIOLATION_CODE)
        log_event(
            logger,
            logging.WARNING,
            "compose_options_uncleared_client_reference_found",
            option=option_label,
            names=[clearance.name for clearance in uncleared],
        )

    violations, dropped = brand_rules.reconcile_violations(violations, text)
    if dropped:
        log_event(
            logger,
            logging.WARNING,
            "compose_options_qa_false_positive_dropped",
            option=option_label,
            dropped_violations=dropped,
        )

    return not violations, violations


def compose_options_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    """Fn 116. thursday-compose-options-{draft} depends_on the matching
    wednesday-draft-{draft} task directly -- the original draft becomes
    option A; two model-written alternates become B and C. Each
    candidate is QA'd independently (_run_option_qa), never aggregate
    (round-34 lesson: see qa_review_brand_steward_handler's own
    history). A card needs >=2 options (contracts/option-card.schema.
    json minItems), so this dead-letters if fewer than 2 of the 3
    candidates survive QA rather than emit an invalid card.

    content_class="public_source_content" on the Options Composer call
    below -- same F-WEEKLY-LOOP-DRAFT-PUBLIC-SOURCE ruling _run_option_
    qa's own docstring cites, not a new one: original_draft_text IS this
    week's already-drafted, brief-derived copy (the same content
    _draft_social_post_handler already sends under that ruling), and
    every option this call's alternates feed into is independently
    reviewed by _run_option_qa before the card is ever built. See
    tests/test_public_source_content_allowlist.py's SIGNED_OFF entry."""
    lineage = resolve_lineage_result(task_id, db)
    if lineage is None:
        raise DispatchError("compose-options: no wednesday-draft ancestor carries a result_ref")
    draft_task, draft_ref = lineage
    vault_asset_id = draft_ref.get("vault_asset_id")
    draft_task_type = draft_task.get("task_type")

    if not vault_asset_id:
        # Mirrors _single_draft_qa_review's own undrafted-vs-missing-asset
        # split: a deliberately-skipped draft (draft_ref carries `status`)
        # completes cleanly -- nothing went wrong, there is simply
        # nothing to compose options from this week.
        with clients.build_vault_client() as vault:
            campaign_id = vault.get_or_create_campaign(
                _campaign_name(envelope), function_id=FUNCTION_ID_116
            )
        db.set_result_ref(
            task_id,
            {
                "composed": False,
                "status": draft_ref.get("status") or "no_reviewable_asset",
                "draft_task_id": draft_task["task_id"],
                "draft_task_type": draft_task_type,
                "campaign_id": campaign_id,
            },
        )
        if draft_ref.get("status"):
            db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
            db.advance_dependents(task_id)
        else:
            db.transition(task_id, TaskStateEnum.FAILED, TransitionReason.QA_BLOCKED)
        return

    channel = _review_channel(draft_task_type)
    proof_points = draft_ref.get("proof_points") or []

    with clients.build_vault_client() as vault, clients.build_gateway_client() as gateway:
        campaign_id = vault.get_or_create_campaign(
            _campaign_name(envelope), function_id=FUNCTION_ID_116
        )
        asset = vault.get_asset(vault_asset_id)
        original_text = _reviewable_draft_text(
            base64.b64decode(asset["content_base64"]).decode("utf-8")
        )

        agent_run = vault.create_agent_run_idempotent(
            task_id=task_id,
            db=db,
            agent_name=_agent_name("options-composer", envelope),
            campaign_id=campaign_id,
            function_id=FUNCTION_ID_116,
            status="running",
            input_payload={
                "draft_task_type": draft_task_type,
                "draft_task_id": draft_task["task_id"],
            },
        )

        payload = {
            "original_draft_text": original_text,
            "pillar": draft_ref.get("pillar"),
            "campaign": draft_ref.get("campaign"),
            "proof_points": proof_points,
        }
        _validate_function_input(FUNCTION_ID_116, payload)

        with emit_task_span(
            "compose-options",
            function_id=FUNCTION_ID_116,
            task_ref=task_id,
            model="claude-sonnet",
            run_id=str(envelope.campaign_id),
        ) as span:
            response, cost = _complete_and_meter(
                gateway,
                vault,
                model="claude-sonnet",
                system_prompt=_read_prompt("116-options-composer"),
                user_content=json.dumps(payload),
                agent_run_id=agent_run["id"],
                content_class="public_source_content",
                max_tokens=3072,
            )
            set_span_attribute(span, "cost", cost)

        output = _parse_json_content(response["content"])
        core._validate_function_output(FUNCTION_ID_116, output)

        candidates = [
            {
                "source": "original",
                "text": original_text,
                "distinctness_axis": None,
                "predicted_outcome": None,
            },
            {
                "source": "alt_1",
                "text": output["alternates"][0]["text"],
                "distinctness_axis": output["alternates"][0]["distinctness_axis"],
                "predicted_outcome": output["alternates"][0]["predicted_outcome"],
            },
            {
                "source": "alt_2",
                "text": output["alternates"][1]["text"],
                "distinctness_axis": output["alternates"][1]["distinctness_axis"],
                "predicted_outcome": output["alternates"][1]["predicted_outcome"],
            },
        ]

        letters = ["A", "B", "C"]
        surviving: list[tuple[str, dict[str, Any]]] = []
        for index, candidate in enumerate(candidates):
            passed, violations = _run_option_qa(
                task_id=task_id,
                db=db,
                vault=vault,
                gateway=gateway,
                envelope=envelope,
                campaign_id=campaign_id,
                channel=channel,
                text=candidate["text"],
                proof_points=proof_points,
                option_label=f"{draft_task_type}:{candidate['source']}",
            )
            if passed:
                surviving.append((letters[index], candidate))
            else:
                log_event(
                    logger,
                    logging.INFO,
                    "compose_options_candidate_qa_failed",
                    task_id=task_id,
                    option=candidate["source"],
                    violations=violations,
                )

        if len(surviving) < 2:
            vault.update_agent_run(
                agent_run["id"],
                status="failed",
                output_payload={"surviving_count": len(surviving)},
                completed_at=_now_iso(),
            )
            db.set_result_ref(
                task_id,
                {
                    "pass": False,
                    "reason": "fewer_than_2_options_survived_qa",
                    "surviving_count": len(surviving),
                    "campaign_id": campaign_id,
                },
            )
            db.transition(task_id, TaskStateEnum.FAILED, TransitionReason.QA_BLOCKED)
            log_event(
                logger,
                logging.INFO,
                "compose_options_blocked",
                task_id=task_id,
                surviving_count=len(surviving),
            )
            return  # never advance_dependents -- friday-route-digest must never see this

        evidence_refs = _option_evidence_refs(proof_points, vault_asset_id)
        options = []
        recommended_source = output["recommended"]
        recommended_letter = None
        for letter, candidate in surviving:
            payload_ref = (
                f"vault://asset/{vault_asset_id}"
                if candidate["source"] == "original"
                else f"vault://agent-run/{agent_run['id']}/{candidate['source']}"
            )
            options.append(
                {
                    "option_id": letter,
                    "label": f"Option {letter}",
                    "summary": candidate["text"][:400],
                    "payload_ref": payload_ref,
                    "evidence_refs": evidence_refs,
                    "predicted_outcome": candidate["predicted_outcome"]
                    or "Engagement in line with this pillar's recent posts.",
                    "risks": [],
                    "distinctness_axis": candidate["distinctness_axis"]
                    or "original draft, unmodified",
                }
            )
            if candidate["source"] == recommended_source:
                recommended_letter = letter
        if recommended_letter is None:
            # The model's own recommendation didn't survive QA -- fall
            # back to whichever candidate DID, deterministically (first
            # surviving letter), rather than fail a good card over one
            # stale field.
            recommended_letter = surviving[0][0]

        card = build_card(
            # NOT content.publish -- that kind is in policies/autonomy-
            # matrix.yaml's non_negotiable_kinds ("publishing from
            # company or personal profiles"), which forces
            # budget_class=realtime and sends every card straight to
            # escalation, bypassing Friday's digest entirely (confirmed
            # the hard way: a manual dry run of route_digest_handler
            # against a content.publish card put it in `escalations`,
            # never `sent`). content.reply is the kind services/
            # options_inbox's own test suite already uses for exactly
            # this "choose among drafted variants" shape, and is not
            # non-negotiable, so it correctly batches into the digest.
            kind="content.reply",
            level=2,
            title=f"{draft_task_type}: choose the version to publish"[:120],
            decision_question="Which version should go out?",
            options=options,
            recommended=recommended_letter,
            evidence_refs=evidence_refs,
            produced_by={"function_id": 116, "prompt_version": "0.1.0"},
            register_rows=["H9"],
            rationale=output.get("rationale", ""),
            lineage={"agent_run_id": agent_run["id"], "source_task_id": task_id},
        )

        created = vault.create_option_card(
            {
                "card_id": card["card_id"],
                "kind": card["kind"],
                "autonomy_level": card["autonomy_level"],
                "risk_tier": card["risk_tier"],
                "agent_run_id": agent_run["id"],
                "produced_by_function": 116,
                "card": card,
                "created_at": card["created_at"],
                "expires_at": card["expires_at"],
            }
        )

        vault.update_agent_run(
            agent_run["id"], status="succeeded", output_payload=output, completed_at=_now_iso()
        )

    db.set_result_ref(
        task_id,
        {
            "pass": True,
            "card_id": created["card_id"],
            "agent_run_id": agent_run["id"],
            "campaign_id": campaign_id,
            "surviving_count": len(surviving),
        },
    )
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)


def route_digest_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    """Fn 117. Pure orchestration, no model call -- see functions/117-
    approval-inbox-router/schema.json's own header. Fetches pending
    OptionCards, applies timeouts and budget via services/options_inbox/
    policy.route(), renders and posts the Teams digest, and writes the
    resulting RoutingResult (shaped to output.schema.json) as this
    task's result_ref.

    Digest rendering/posting is skipped -- gracefully, not an error --
    when either CMOS_APPROVAL_BASE_URL or TEAMS_WEBHOOK_URL is unset,
    mirroring notify_brief_ready's own "zero POSTs when unset" contract
    (AC-25). The routing MATH still runs and is still recorded either
    way: ranking/budgeting/timeout decisions are real work independent
    of whether Teams is configured yet in this environment."""
    with clients.build_vault_client() as vault:
        campaign_id = vault.get_or_create_campaign(
            _campaign_name(envelope), function_id=FUNCTION_ID_117
        )
        agent_run = vault.create_agent_run_idempotent(
            task_id=task_id,
            db=db,
            agent_name=_agent_name("approval-inbox-router", envelope),
            campaign_id=campaign_id,
            function_id=FUNCTION_ID_117,
            status="running",
            input_payload={},
        )

        pending_rows = vault.list_pending_option_cards(limit=500)
        cards = [row["card"] for row in pending_rows]

        now = datetime.now(timezone.utc)
        # Standing permissions always [] this session -- Fn 118's seed
        # loop (App D PR 10-13) is what will ever populate real ones;
        # see the module-section docstring above.
        routing = route(cards, [], now=now)

        digest_date = now.date().isoformat()
        output = {
            "digest_date": digest_date,
            "sent": [c["card_id"] for c in routing.sent],
            "auto_resolved_by_permission": [
                {"card_id": c["card_id"], "permission_id": permission_id}
                for c, permission_id in routing.auto_resolved
            ],
            "timeout_defaults_applied": [c["card_id"] for c in routing.timeout_defaults],
            "expired_unresolved": [c["card_id"] for c in routing.expired_unresolved],
            "queued_overflow_count": len(routing.queued_overflow),
            "budget_used": len(routing.sent),
            "escalations": [c["card_id"] for c in routing.escalations],
        }
        core._validate_function_output(FUNCTION_ID_117, output)

        posted = False
        approval_base_url = os.environ.get("CMOS_APPROVAL_BASE_URL")
        if approval_base_url and routing.sent:
            with clients.build_gatekeeper_client() as gatekeeper:
                digest = _render_options_digest(
                    routing.sent,
                    approval_base_url=approval_base_url,
                    gatekeeper=gatekeeper,
                    overflow_count=len(routing.queued_overflow),
                    digest_date=digest_date,
                )
            from orchestrator import teams_notify

            posted = teams_notify.notify_options_digest(digest=digest, card_count=len(routing.sent))

        vault.update_agent_run(
            agent_run["id"], status="succeeded", output_payload=output, completed_at=_now_iso()
        )

    output["posted"] = posted
    db.set_result_ref(
        task_id, {**output, "agent_run_id": agent_run["id"], "campaign_id": campaign_id}
    )
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)


def _render_options_digest(
    sent_cards: list[dict[str, Any]],
    *,
    approval_base_url: str,
    gatekeeper: GatekeeperClient,
    overflow_count: int,
    digest_date: str,
) -> dict[str, Any]:
    """services/options_inbox/teams_render.render_digest needs a
    `signer(card_id) -> str` callable; the real RS256 signing key only
    ca-gatekeeper's identity can reach (Key Vault), so this calls its
    new internal POST /sign-option-card-link once per card rather than
    signing locally (see GatekeeperClient.sign_option_card_link's own
    docstring)."""
    from options_inbox.teams_render import render_digest

    return render_digest(
        sent_cards,
        approval_base_url=approval_base_url,
        signer=gatekeeper.sign_option_card_link,
        overflow_count=overflow_count,
        digest_date=digest_date,
    )


