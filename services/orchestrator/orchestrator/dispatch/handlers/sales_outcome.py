from __future__ import annotations

from typing import Any

from orchestrator.models import TaskEnvelope, TaskStateEnum, TransitionReason

from .. import clients
from ..core import _agent_name, _campaign_name

# ---------------------------------------------------------------------
# Fn 120 -- Sales Outcome Inferencer (Appendix D PR 11)
# ---------------------------------------------------------------------
#
# NOT WIRED, DOCUMENTED (a confidentiality decision, same class as Fn
# 113's own Fireflies gap -- see that section's module docstring). Every
# approved input source (crm_record, fireflies_transcript, email_thread,
# teams_message) is either a live vendor API this repo has no
# provisioned credentials for, or real prospect/client sales
# conversations -- worse than Fn 113's case, since a lead's acceptance/
# stage/win-loss reasoning is client-identifying BY DEFINITION, not
# merely adjacent to it. Unlike Fn 113, there is no safe, already-public
# substitute here at all (no "docs/positioning.md" equivalent for real
# sales data exists or could exist). Fabricating example CRM data to
# give this function something to do would itself be the exact harm its
# own guardrail names ("north-star pipeline numbers are never reported
# from unconfirmed inferences") -- so this handler calls no model and
# builds no card; it only reports the honest gap.

FUNCTION_ID_120 = "120-sales-outcome-inferencer"


def sales_outcome_infer_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    with clients.build_vault_client() as vault:
        campaign_id = vault.get_or_create_campaign(
            _campaign_name(envelope), function_id=FUNCTION_ID_120
        )
        agent_run = vault.create_agent_run_idempotent(
            task_id=task_id,
            db=db,
            agent_name=_agent_name("sales-outcome-inferencer", envelope),
            campaign_id=campaign_id,
            function_id=FUNCTION_ID_120,
            status="succeeded",
            input_payload={},
            output_payload={"status": "not_configured"},
        )
    db.set_result_ref(
        task_id,
        {
            "status": "not_configured",
            "reason": (
                "no CRM/Fireflies/email/Teams integration is provisioned -- see "
                "dispatch.py's own module-section docstring above FUNCTION_ID_120"
            ),
            "agent_run_id": agent_run["id"],
            "campaign_id": campaign_id,
        },
    )
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)


