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

# ---------------------------------------------------------------------
# Fn 125 -- Incident Autopilot (Appendix D PR 11)
# ---------------------------------------------------------------------
#
# SCOPE CUT, DOCUMENTED. prompt.md's triggers (a guardrail breach, an
# anomaly from Fn 100, a reputation alert from Fn 75) name functions that
# do not exist in this repo, and no live detector anywhere watches
# published content for a breach after the fact (every existing QA gate
# runs BEFORE publish). This PR wires Diagnose + Draft-recovery-as-
# options for a MANUALLY-SUPPLIED incident report (envelope.metadata,
# the same mechanism proof_circuit already uses) plus a real standing-
# permission suspend side effect -- never an automatic trigger this repo
# has no detector to drive, and never the "pause the affected lane"
# Contain step, which needs a kill-switch mechanism this repo does not
# have either. Registered and directly testable, no scheduled loop
# entry -- report_month_end_handler's own precedent for a real,
# dispatch-ready handler with no wired trigger yet.

FUNCTION_ID_125 = "125-incident-autopilot"
STANDING_PERMISSION_SUSPENDED_SIGNAL_TYPE = "standing_permission_suspended"


def incident_diagnose_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    metadata = envelope.metadata or {}
    incident_description = metadata.get("incident_description")
    if not incident_description:
        db.set_result_ref(
            task_id,
            {
                "status": "no_incident_reported",
                "reason": (
                    "envelope.metadata carries no incident_description -- this task "
                    "has no automatic trigger (see dispatch.py's own module-section "
                    "docstring above FUNCTION_ID_125)"
                ),
            },
        )
        db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
        db.advance_dependents(task_id)
        return

    producing_function_id = int(metadata.get("producing_function_id") or 0)
    reached_an_audience = str(metadata.get("reached_an_audience", "true")).lower() != "false"
    permission_id_to_suspend = metadata.get("permission_id_to_suspend")

    with clients.build_vault_client() as vault, clients.build_gateway_client() as gateway:
        campaign_id = vault.get_or_create_campaign(
            _campaign_name(envelope), function_id=FUNCTION_ID_125
        )
        suspended = None
        if permission_id_to_suspend:
            vault.create_signal(
                source=f"function-{FUNCTION_ID_125}",
                signal_type=STANDING_PERMISSION_SUSPENDED_SIGNAL_TYPE,
                payload={
                    "permission_id": permission_id_to_suspend,
                    "reason": incident_description,
                    "suspended_at": _now_iso(),
                },
                campaign_id=campaign_id,
                function_id=FUNCTION_ID_125,
            )
            suspended = permission_id_to_suspend

        agent_run = vault.create_agent_run_idempotent(
            task_id=task_id,
            db=db,
            agent_name=_agent_name("incident-autopilot", envelope),
            campaign_id=campaign_id,
            function_id=FUNCTION_ID_125,
            status="running",
            input_payload={
                "incident_description": incident_description,
                "producing_function_id": producing_function_id,
            },
        )
        payload = {
            "incident_description": incident_description,
            "producing_function_id": producing_function_id,
            "reached_an_audience": reached_an_audience,
        }
        _validate_function_input(FUNCTION_ID_125, payload)

        with emit_task_span(
            "incident-diagnose",
            function_id=FUNCTION_ID_125,
            task_ref=task_id,
            model="claude-sonnet",
            run_id=str(envelope.campaign_id),
        ) as span:
            response, cost = _complete_and_meter(
                gateway,
                vault,
                model="claude-sonnet",
                system_prompt=_read_prompt(FUNCTION_ID_125),
                user_content=json.dumps(payload),
                agent_run_id=agent_run["id"],
                max_tokens=3072,
            )
            set_span_attribute(span, "cost", cost)

        output = _parse_json_content(response["content"])
        core._validate_function_output(FUNCTION_ID_125, output)

        if len(output["options"]) < 2:
            vault.update_agent_run(
                agent_run["id"],
                status="failed",
                output_payload=output,
                completed_at=_now_iso(),
            )
            db.set_result_ref(
                task_id,
                {
                    "status": "insufficient_options",
                    "option_count": len(output["options"]),
                    "campaign_id": campaign_id,
                },
            )
            db.transition(task_id, TaskStateEnum.FAILED, TransitionReason.QA_BLOCKED)
            return

        label_to_letter = {
            "correct_in_place": "A",
            "delete_and_reissue": "B",
            "delete_silently": "C",
        }
        evidence_refs = [
            {
                "source_type": "vault_asset",
                "ref": f"vault://agent-run/{agent_run['id']}",
                "quote": output["rationale"][:300],
                "authority": "primary",
            }
        ]
        options = []
        for position in output["options"]:
            option_label = position["label"]
            if option_label == "delete_silently" and reached_an_audience:
                continue  # prompt.md: recommend only when nothing reached an audience
            options.append(
                {
                    "option_id": label_to_letter[option_label],
                    "label": option_label.replace("_", " "),
                    "summary": position["argument"][:400],
                    "payload_ref": f"vault://agent-run/{agent_run['id']}",
                    "evidence_refs": evidence_refs,
                    "predicted_outcome": position["argument"][:300],
                    "risks": [],
                    "distinctness_axis": option_label.replace("_", " "),
                }
            )

        if len(options) < 2:
            vault.update_agent_run(
                agent_run["id"],
                status="failed",
                output_payload=output,
                completed_at=_now_iso(),
            )
            db.set_result_ref(
                task_id,
                {
                    "status": "insufficient_options",
                    "option_count": len(options),
                    "campaign_id": campaign_id,
                },
            )
            db.transition(task_id, TaskStateEnum.FAILED, TransitionReason.QA_BLOCKED)
            return

        option_ids = {o["option_id"] for o in options}
        recommended = label_to_letter.get(output["recommended_option"])
        if recommended not in option_ids:
            recommended = options[0]["option_id"]

        card = build_card(
            kind="crisis.correction",
            level=0,  # overridden to non_negotiable/realtime by build_card itself
            title=f"Incident: {output['failure_class']}"[:120],
            decision_question="How should this incident's affected content be corrected?",
            options=options,
            recommended=recommended,
            evidence_refs=evidence_refs,
            produced_by={"function_id": 125, "prompt_version": "0.1.0"},
            register_rows=["H17"],
            rationale=output["rationale"],
            context_summary=f"failure_class={output['failure_class']}",
            lineage={"agent_run_id": agent_run["id"], "source_task_id": task_id},
        )
        created = vault.create_option_card(
            {
                "card_id": card["card_id"],
                "kind": card["kind"],
                "autonomy_level": card["autonomy_level"],
                "risk_tier": card["risk_tier"],
                "agent_run_id": agent_run["id"],
                "produced_by_function": 125,
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
            "status": "diagnosed",
            "card_id": created["card_id"],
            "failure_class": output["failure_class"],
            "suspended_permission_id": suspended,
            "campaign_id": campaign_id,
        },
    )
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)


