from __future__ import annotations

import json
from typing import Any

from options_inbox.cards import build_card
from telemetry_lib import set_span_attribute

from orchestrator.clients.vault_client_ext import VaultClientExt
from orchestrator.models import TaskEnvelope, TaskStateEnum, TransitionReason
from orchestrator.telemetry_wiring import emit_task_span

from .. import clients, core
from ..completion import _complete_and_meter
from ..core import (
    _agent_name,
    _campaign_name,
    _now_iso,
    _parse_json_content,
    _read_prompt,
    _validate_function_input,
)
from .source_lifecycle import LIFECYCLE_SIGNAL_LOOKBACK

# ---------------------------------------------------------------------
# Fn 124 -- Legal Triage (Appendix D PR 11)
# ---------------------------------------------------------------------
#
# INTEGRATION SCOPE, DOCUMENTED (same shape as Fn 129's own relationship
# to Fn 128). prompt.md's own words describe a synchronous gate ("every
# option payload passes through you before the card is emitted") --
# retrofitting that into every existing card-producing handler (Fn 116,
# 115, 118, 127, 128, 129) is a shared-mechanism change across six call
# sites, exactly what this repo's own hard rules say needs auditing
# first, not a one-PR addition. This PR instead runs Fn 124 as an
# independent sweep over whatever OptionCards are currently pending,
# tagging each with a real, model-produced GREEN/AMBER/RED verdict it has
# not already tagged (LIFECYCLE_SIGNAL_LOOKBACK-bounded, same convention
# as every other "have I already handled this" check in this file) and
# emitting the appropriate legal.amber / legal.sensitive_statement card
# for anything above GREEN.

FUNCTION_ID_124 = "124-legal-triage"
LEGAL_TRIAGE_VERDICT_SIGNAL_TYPE = "legal_triage_verdict"


def _pending_card_text(card: dict[str, Any]) -> str:
    parts = [
        str(card.get("title", "")),
        str(card.get("decision_question", "")),
        str(card.get("rationale", "") or card.get("recommendation_rationale", "")),
    ]
    for option in card.get("options") or []:
        parts.append(str(option.get("summary", "")))
    return "\n".join(part for part in parts if part)


def _already_triaged_card_ids(vault: VaultClientExt) -> set[str]:
    triaged: set[str] = set()
    for row in vault.list_signals(limit=LIFECYCLE_SIGNAL_LOOKBACK):
        if row.get("signal_type") == LEGAL_TRIAGE_VERDICT_SIGNAL_TYPE:
            card_id = (row.get("payload") or {}).get("card_id")
            if card_id:
                triaged.add(str(card_id))
    return triaged


def legal_triage_sweep_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    with clients.build_vault_client() as vault, clients.build_gateway_client() as gateway:
        campaign_id = vault.get_or_create_campaign(
            _campaign_name(envelope), function_id=FUNCTION_ID_124
        )
        already_triaged = _already_triaged_card_ids(vault)
        pending_rows = vault.list_pending_option_cards(limit=500)

        green_count = 0
        amber_cards: list[dict[str, Any]] = []
        red_cards: list[dict[str, Any]] = []

        for row in pending_rows:
            card_id = row["card_id"]
            if card_id in already_triaged:
                continue
            source_card = row["card"]
            kind = source_card.get("kind", "")
            text = _pending_card_text(source_card)

            agent_run = vault.create_agent_run_idempotent(
                task_id=task_id,
                db=db,
                agent_name=_agent_name("legal-triage", envelope),
                campaign_id=campaign_id,
                function_id=FUNCTION_ID_124,
                status="running",
                input_payload={"source_card_id": card_id, "card_kind": kind},
            )
            payload = {"card_kind": kind, "text": text}
            _validate_function_input(FUNCTION_ID_124, payload)

            with emit_task_span(
                "legal-triage",
                function_id=FUNCTION_ID_124,
                task_ref=task_id,
                model="claude-haiku",
                run_id=str(envelope.campaign_id),
            ) as span:
                response, cost = _complete_and_meter(
                    gateway,
                    vault,
                    model="claude-haiku",
                    system_prompt=_read_prompt(FUNCTION_ID_124),
                    user_content=json.dumps(payload),
                    agent_run_id=agent_run["id"],
                    max_tokens=2048,
                )
                set_span_attribute(span, "cost", cost)

            output = _parse_json_content(response["content"])
            core._validate_function_output(FUNCTION_ID_124, output)
            vault.update_agent_run(
                agent_run["id"], status="succeeded", output_payload=output, completed_at=_now_iso()
            )

            vault.create_signal(
                source=f"function-{FUNCTION_ID_124}",
                signal_type=LEGAL_TRIAGE_VERDICT_SIGNAL_TYPE,
                payload={
                    "card_id": card_id,
                    "tier": output["tier"],
                    "rule_cited": output["rule_cited"],
                },
                campaign_id=campaign_id,
                function_id=FUNCTION_ID_124,
            )

            if output["tier"] == "GREEN":
                green_count += 1
                continue

            evidence_ref = f"vault://agent-run/{agent_run['id']}"
            evidence_refs = [
                {
                    "source_type": "vault_asset",
                    "ref": evidence_ref,
                    "quote": output["rationale"][:300],
                    "authority": "primary",
                }
            ]
            context_summary = f"Tier {output['tier']}: {output['rule_cited']}"

            if output["tier"] == "AMBER":
                options = [
                    {
                        "option_id": "A",
                        "label": "Publish as is",
                        "summary": "Publish the payload without modification."[:400],
                        "payload_ref": evidence_ref,
                        "evidence_refs": evidence_refs,
                        "predicted_outcome": "Ships with the AMBER-tier language unchanged.",
                        "risks": [output["rule_cited"][:200]],
                        "distinctness_axis": "unchanged",
                    },
                    {
                        "option_id": "B",
                        "label": "Publish with softening",
                        "summary": (output.get("softened_text") or "Publish a softened version.")[
                            :400
                        ],
                        "payload_ref": evidence_ref,
                        "evidence_refs": evidence_refs,
                        "predicted_outcome": "Ships with the specific softening drafted.",
                        "risks": [],
                        "distinctness_axis": "softened language",
                    },
                    {
                        "option_id": "C",
                        "label": "Hold",
                        "summary": "Do not publish this payload.".strip()[:400],
                        "payload_ref": evidence_ref,
                        "evidence_refs": evidence_refs,
                        "predicted_outcome": "No change; this card stays unresolved.",
                        "risks": [],
                        "distinctness_axis": "hold, no publication",
                    },
                ]
                card = build_card(
                    kind="legal.amber",
                    level=0,
                    title=f"AMBER: {context_summary}"[:120],
                    decision_question="Publish this AMBER-tier payload, softened, or hold?",
                    options=options,
                    recommended="B",
                    evidence_refs=evidence_refs,
                    produced_by={"function_id": 124, "prompt_version": "0.1.0"},
                    register_rows=["H15"],
                    rationale=output["rationale"],
                    context_summary=context_summary,
                    lineage={"agent_run_id": agent_run["id"], "source_task_id": task_id},
                )
            else:  # RED
                options = [
                    {
                        "option_id": "A",
                        "label": "Send to counsel",
                        "summary": "Escalate to outside counsel before any decision.".strip()[:400],
                        "payload_ref": evidence_ref,
                        "evidence_refs": evidence_refs,
                        "predicted_outcome": "Nothing publishes until counsel responds.",
                        "risks": [],
                        "distinctness_axis": "escalate",
                    },
                    {
                        "option_id": "B",
                        "label": "Withdraw the asset",
                        "summary": "Withdraw this payload; do not publish.".strip()[:400],
                        "payload_ref": evidence_ref,
                        "evidence_refs": evidence_refs,
                        "predicted_outcome": "This payload never reaches an audience.",
                        "risks": [],
                        "distinctness_axis": "withdraw",
                    },
                ]
                card = build_card(
                    kind="legal.sensitive_statement",
                    level=0,  # overridden to non_negotiable/realtime by build_card itself
                    title=f"RED: {context_summary}"[:120],
                    decision_question="Send to counsel, or withdraw this payload?",
                    options=options,
                    recommended="A",
                    evidence_refs=evidence_refs,
                    produced_by={"function_id": 124, "prompt_version": "0.1.0"},
                    register_rows=["H15"],
                    rationale=output["rationale"],
                    context_summary=context_summary,
                    lineage={"agent_run_id": agent_run["id"], "source_task_id": task_id},
                )

            created = vault.create_option_card(
                {
                    "card_id": card["card_id"],
                    "kind": card["kind"],
                    "autonomy_level": card["autonomy_level"],
                    "risk_tier": card["risk_tier"],
                    "agent_run_id": agent_run["id"],
                    "produced_by_function": 124,
                    "card": card,
                    "created_at": card["created_at"],
                    "expires_at": card["expires_at"],
                }
            )
            triage_summary = {"source_card_id": card_id, "triage_card_id": created["card_id"]}
            (amber_cards if output["tier"] == "AMBER" else red_cards).append(triage_summary)

    db.set_result_ref(
        task_id,
        {
            "status": "swept",
            "green_count": green_count,
            "amber": amber_cards,
            "red": red_cards,
            "campaign_id": campaign_id,
        },
    )
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)


