from __future__ import annotations

import json
from typing import Any

from options_inbox.cards import build_card
from telemetry_lib import set_span_attribute

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
from .voice_model import _latest_voice_profile

# ---------------------------------------------------------------------
# Fn 119 -- Client Permission Agent (Appendix D PR 12)
# ---------------------------------------------------------------------
#
# Manually triggered only (envelope.metadata, the same mechanism Fn 125
# already uses) -- see functions/119-client-permission-agent/schema.json's
# own description for the full reasoning: prompt.md names Fn 26 (Client
# Advocacy Harvester) and Fn 47 (Case Study Writer) as its triggers, and
# both already exist in this dispatch table, but neither ever reaches a
# state that would call this function today.
# draft_client_advocacy_harvest_handler's own docstring says it
# "reliably returns naming_decision=blocked-no-consent every real run"
# (no consent-record fixture wired); _build_case_study_payload
# unconditionally raises DraftNotAttempted("no_cleared_engagement", ...)
# because docs/permission-register.yaml is default-deny and nothing in
# it is CLEARED. Registered and directly testable, no scheduled loop
# entry -- the same precedent Fn 125 already set for a real,
# dispatch-ready handler with no wired trigger yet.
#
# Never writes docs/permission-register.yaml (read-only, via
# functions/02-brand-steward-qa/permission_check.py's already-shipped
# load_permission_check()/check_clearance()) and never sends anything:
# no Outlook/email-send integration exists anywhere in this repository,
# so prompt.md's "on chosen A or B, hand the email to the publisher's
# Outlook send path" is not built here -- this proposes a
# client.permission_request card and stops.

FUNCTION_ID_119 = "119-client-permission-agent"
_PERMISSION_REQUEST_LABEL_TO_OPTION_ID = {
    "named_case_study": "A",
    "named_logo_and_quote": "B",
    "anonymised_only": "C",
}


def client_permission_request_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    metadata = envelope.metadata or {}
    client_name = str(metadata.get("client_name") or "").strip()
    context = str(metadata.get("context") or "").strip()
    requested_use = str(metadata.get("requested_use") or "").strip()

    if not client_name or not context:
        db.set_result_ref(
            task_id,
            {
                "status": "no_request_reported",
                "reason": (
                    "envelope.metadata requires client_name and context -- "
                    "neither is invented by this function"
                ),
            },
        )
        db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
        db.advance_dependents(task_id)
        return

    permission_check = clients.load_permission_check()
    clearance = permission_check.check_clearance(client_name)

    with clients.build_vault_client() as vault:
        campaign_id = vault.get_or_create_campaign(
            _campaign_name(envelope), function_id=FUNCTION_ID_119
        )

        if clearance.allowed:
            agent_run = vault.create_agent_run_idempotent(
                task_id=task_id,
                db=db,
                agent_name=_agent_name("client-permission-agent", envelope),
                campaign_id=campaign_id,
                function_id=FUNCTION_ID_119,
                status="succeeded",
                input_payload={"client_name": client_name, "context": context},
                output_payload={"status": "already_permitted"},
            )
            db.set_result_ref(
                task_id,
                {
                    "status": "already_permitted",
                    "clearance_status": clearance.status,
                    "agent_run_id": agent_run["id"],
                    "campaign_id": campaign_id,
                },
            )
            db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
            db.advance_dependents(task_id)
            return

        voice_profile = _latest_voice_profile(vault)
        agent_run = vault.create_agent_run_idempotent(
            task_id=task_id,
            db=db,
            agent_name=_agent_name("client-permission-agent", envelope),
            campaign_id=campaign_id,
            function_id=FUNCTION_ID_119,
            status="running",
            input_payload={
                "client_name": client_name,
                "context": context,
                "clearance_status": clearance.status,
            },
        )
        payload = {
            "client_name": client_name,
            "context": context,
            "requested_use": requested_use,
            "clearance_status": clearance.status,
            "clearance_reason": clearance.reason,
            "voice_profile": voice_profile,
        }
        _validate_function_input(FUNCTION_ID_119, payload)

        with clients.build_gateway_client() as gateway:
            with emit_task_span(
                "client-permission-request",
                function_id=FUNCTION_ID_119,
                task_ref=task_id,
                model="claude-sonnet",
                run_id=str(envelope.campaign_id),
            ) as span:
                response, cost = _complete_and_meter(
                    gateway,
                    vault,
                    model="claude-sonnet",
                    system_prompt=_read_prompt(FUNCTION_ID_119),
                    user_content=json.dumps(payload),
                    agent_run_id=agent_run["id"],
                    max_tokens=2048,
                )
                set_span_attribute(span, "cost", cost)

        output = _parse_json_content(response["content"])
        core._validate_function_output(FUNCTION_ID_119, output)

        shared_evidence = [
            {
                "source_type": "permission_register",
                "ref": "docs/permission-register.yaml",
                "quote": clearance.reason[:300],
                "authority": "primary",
            }
        ]
        options = []
        for entry in output["options"]:
            option_id = _PERMISSION_REQUEST_LABEL_TO_OPTION_ID[entry["label"]]
            options.append(
                {
                    "option_id": option_id,
                    "label": entry["label"].replace("_", " "),
                    "summary": entry["argument"][:400],
                    "payload_ref": f"vault://agent-run/{agent_run['id']}",
                    "evidence_refs": shared_evidence,
                    "predicted_outcome": (
                        "Pieter chooses a request strategy; sending it is a "
                        "documented follow-up (no Outlook/email-send path exists)."
                    ),
                    "risks": [
                        "Client declines or does not respond -- no reply-tracking exists yet."
                    ],
                    "distinctness_axis": "how much of the client is named",
                }
            )
        recommended_id = _PERMISSION_REQUEST_LABEL_TO_OPTION_ID.get(
            output["recommended_option"], options[0]["option_id"]
        )
        anonymised = output["anonymised_path"]

        card = build_card(
            kind="client.permission_request",
            level=1,
            title=f"Ask {client_name} for reference permission"[:120],
            decision_question=f"How should we ask {client_name} to be named, if at all?",
            options=options,
            recommended=recommended_id,
            evidence_refs=shared_evidence,
            produced_by={"function_id": 119, "prompt_version": "0.1.0"},
            register_rows=["H7", "H18"],
            rationale=output["rationale"][:600],
            context_summary=(
                f"{context}. Anonymised-path combination test: "
                f"{'passes' if anonymised['passes_combination_test'] else 'fails'} -- "
                f"{anonymised['notes']}"
            )[:1200],
            lineage={"agent_run_id": agent_run["id"], "source_task_id": task_id},
        )
        created = vault.create_option_card(
            {
                "card_id": card["card_id"],
                "kind": card["kind"],
                "autonomy_level": card["autonomy_level"],
                "risk_tier": card["risk_tier"],
                "agent_run_id": agent_run["id"],
                "produced_by_function": 119,
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
            "status": "requested",
            "card_id": created["card_id"],
            "clearance_status": clearance.status,
            "campaign_id": campaign_id,
        },
    )
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)


