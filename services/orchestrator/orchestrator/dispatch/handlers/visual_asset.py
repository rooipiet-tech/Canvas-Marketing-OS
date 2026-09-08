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
from .canva import CANVA_MANIFEST_TEMPLATE_COLUMN

# ---------------------------------------------------------------------
# Fn 121 -- Visual Asset Composer (Appendix D PR 12)
# ---------------------------------------------------------------------
#
# SCOPE CUT, see functions/121-visual-asset-composer/schema.json's own
# description for the full reasoning: no template-library asset files
# (the "~34 Canvas for X" lockups, device-mockup library, approved
# anonymised Power BI screenshots, icon set) exist anywhere in this
# repo, so this drafts Canva Bulk Create CSV row-set options only --
# function 45's own already-shipped manifest shape -- never a free-form
# render, and never runs the deterministic palette/clear-space/contrast/
# OCR checks prompt.md describes (nothing real to check without either
# those files or a live Canva render, and the latter is blocked:
# canva_dry_run() stays true pending the "Canva refresh token" decision
# the blueprint's own change log names as "unlock C; blocks Fn 121").
# Manually triggered only (envelope.metadata) -- no function in this
# dispatch table has an "on card chosen" callback today (Fn 114/115/118
# set the same precedent), so a chosen option is never itself submitted
# to mcp-canva here.

FUNCTION_ID_121 = "121-visual-asset-composer"


def visual_asset_compose_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    metadata = envelope.metadata or {}
    asset_type = str(metadata.get("asset_type") or "").strip()
    brand_template_id = str(metadata.get("brand_template_id") or "").strip()
    copy_source = str(metadata.get("copy_source") or "").strip()
    proof_points = metadata.get("proof_points") or []

    if not asset_type or not brand_template_id or not copy_source:
        db.set_result_ref(
            task_id,
            {
                "status": "not_configured",
                "reason": (
                    "envelope.metadata requires asset_type, brand_template_id and "
                    "copy_source -- this repo has no template catalog to look a "
                    "template id up from, so it must be supplied by the caller"
                ),
            },
        )
        db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
        db.advance_dependents(task_id)
        return

    with clients.build_vault_client() as vault:
        campaign_id = vault.get_or_create_campaign(
            _campaign_name(envelope), function_id=FUNCTION_ID_121
        )
        agent_run = vault.create_agent_run_idempotent(
            task_id=task_id,
            db=db,
            agent_name=_agent_name("visual-asset-composer", envelope),
            campaign_id=campaign_id,
            function_id=FUNCTION_ID_121,
            status="running",
            input_payload={"asset_type": asset_type, "brand_template_id": brand_template_id},
        )
        payload = {
            "asset_type": asset_type,
            "brand_template_id": brand_template_id,
            "copy_source": copy_source,
            "proof_points": proof_points,
        }
        _validate_function_input(FUNCTION_ID_121, payload)

        with clients.build_gateway_client() as gateway:
            with emit_task_span(
                "visual-asset-compose",
                function_id=FUNCTION_ID_121,
                task_ref=task_id,
                model="claude-sonnet",
                run_id=str(envelope.campaign_id),
            ) as span:
                response, cost = _complete_and_meter(
                    gateway,
                    vault,
                    model="claude-sonnet",
                    system_prompt=_read_prompt(FUNCTION_ID_121),
                    user_content=json.dumps(payload),
                    agent_run_id=agent_run["id"],
                    max_tokens=2048,
                )
                set_span_attribute(span, "cost", cost)

        output = _parse_json_content(response["content"])
        core._validate_function_output(FUNCTION_ID_121, output)

        shared_evidence = [
            {
                "source_type": "vault_asset",
                "ref": f"vault://agent-run/{agent_run['id']}",
                "authority": "primary",
            }
        ]
        letters = ["A", "B", "C"]
        options = []
        label_to_option_id: dict[str, str] = {}
        for index, entry in enumerate(output["options"]):
            option_id = letters[index]
            label_to_option_id[entry["label"]] = option_id
            csv_row = dict(entry["csv_row"])
            csv_row[CANVA_MANIFEST_TEMPLATE_COLUMN] = brand_template_id
            options.append(
                {
                    "option_id": option_id,
                    "label": entry["label"][:60],
                    "summary": entry["argument"][:400],
                    "payload_ref": f"vault://agent-run/{agent_run['id']}#option-{option_id}",
                    "evidence_refs": shared_evidence,
                    "predicted_outcome": (
                        "A Canva Bulk Create row ready for mcp-canva; never submitted "
                        "automatically -- proposes only."
                    ),
                    "risks": ["No deterministic brand check ran -- no rendered asset exists yet."],
                    "distinctness_axis": output["axis"][:80],
                }
            )
        recommended_id = label_to_option_id.get(
            output["recommended_option"], options[0]["option_id"]
        )

        card = build_card(
            kind="content.visual_variant",
            level=1,
            title=f"Visual variants for {asset_type.replace('_', ' ')}"[:120],
            decision_question="Which visual variant should carry this content?",
            options=options,
            recommended=recommended_id,
            evidence_refs=shared_evidence,
            produced_by={"function_id": 121, "prompt_version": "0.1.0"},
            register_rows=["H10"],
            rationale=output["rationale"][:600],
            context_summary=f"asset_type={asset_type}, brand_template_id={brand_template_id}"[
                :1200
            ],
            lineage={"agent_run_id": agent_run["id"], "source_task_id": task_id},
        )
        created = vault.create_option_card(
            {
                "card_id": card["card_id"],
                "kind": card["kind"],
                "autonomy_level": card["autonomy_level"],
                "risk_tier": card["risk_tier"],
                "agent_run_id": agent_run["id"],
                "produced_by_function": 121,
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
        {"status": "composed", "card_id": created["card_id"], "campaign_id": campaign_id},
    )
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)


