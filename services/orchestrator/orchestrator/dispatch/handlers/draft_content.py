from __future__ import annotations

import json
from typing import Any

from telemetry_lib import set_span_attribute

from orchestrator.models import TaskEnvelope, TaskStateEnum, TransitionReason
from orchestrator.telemetry_wiring import emit_task_span

from .. import clients
from ..completion import _complete_and_meter
from ..core import (
    FUNCTION_ID_42,
    PROOF_CIRCUIT_TAG,
    _agent_name,
    _campaign_name,
    _now_iso,
    _parse_json_content,
    _read_prompt,
)

# ---------------------------------------------------------------------
# draft-content (plan step 11; AC-01, AC-06, AC-30) -- the S8 proof
# circuit's own function-42 draft. Client-free by construction (DE-4):
# no client_reference is ever populated, so it legitimately clears
# function 02's uncleared-client-block rather than needing an override.
# ---------------------------------------------------------------------

# A generic, client-free proof point drawn from docs/positioning.md
# section 3 (Imperial's architecture facts, described WITHOUT naming the
# client -- DE-4/AC-06). Never invented: these are the same numbers
# positioning.md's own pillar table cites for "Consolidation at scale".
DRAFT_CONTENT_PROOF_POINT = (
    "one recent engagement consolidated 40+ business units across 14+ ERP systems "
    "into a single governed Azure lakehouse"
)
DRAFT_CONTENT_PILLAR = "Consolidation at scale"
DRAFT_CONTENT_CAMPAIGN_UTM = "loop-proof"

def draft_content_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    with clients.build_vault_client() as vault:
        campaign_id = vault.get_or_create_campaign(
            _campaign_name(envelope), function_id=FUNCTION_ID_42
        )

        agent_run = vault.create_agent_run_idempotent(
            task_id=task_id,
            db=db,
            agent_name=_agent_name("linkedin-post-writer", envelope),
            campaign_id=campaign_id,
            function_id=FUNCTION_ID_42,
            status="running",
            input_payload={
                "pillar": DRAFT_CONTENT_PILLAR,
                "campaign": DRAFT_CONTENT_CAMPAIGN_UTM,
                "proof_circuit_tag": PROOF_CIRCUIT_TAG,
            },
        )

        system_prompt = _read_prompt("42-linkedin-post-writer")
        user_content = json.dumps(
            {
                "pillar": DRAFT_CONTENT_PILLAR,
                "proof_point": DRAFT_CONTENT_PROOF_POINT,
                "campaign": DRAFT_CONTENT_CAMPAIGN_UTM,
            }
        )

        with emit_task_span(
            "draft-content",
            function_id=FUNCTION_ID_42,
            task_ref=task_id,
            model="claude-sonnet",
            run_id=str(envelope.campaign_id),
        ) as span:
            with clients.build_gateway_client() as gateway:
                response, cost = _complete_and_meter(
                    gateway,
                    vault,
                    model="claude-sonnet",
                    system_prompt=system_prompt,
                    user_content=user_content,
                    agent_run_id=agent_run["id"],
                )
            set_span_attribute(span, "cost", cost)

        output = _parse_json_content(response["content"])
        post_text = output["post"]
        post_bytes = post_text.encode("utf-8")

        asset = vault.create_asset(
            asset_type="linkedin_post",
            agent_run_id=agent_run["id"],
            campaign_id=campaign_id,
            function_id=FUNCTION_ID_42,
            content_bytes=post_bytes,
            approval_state="draft",
        )
        vault.update_agent_run(
            agent_run["id"], status="succeeded", output_payload=output, completed_at=_now_iso()
        )

    db.set_result_ref(
        task_id,
        {
            "vault_asset_id": asset["id"],
            "content_hash": asset["content_hash"],
            "agent_run_id": agent_run["id"],
            "campaign_id": campaign_id,
        },
    )
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)

