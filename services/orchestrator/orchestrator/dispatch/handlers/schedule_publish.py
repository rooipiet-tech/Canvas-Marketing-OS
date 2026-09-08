from __future__ import annotations

import logging
from typing import Any

from orchestrator.dispatch_errors import DispatchError
from orchestrator.logging_config import log_event
from orchestrator.models import TaskEnvelope, TaskStateEnum, TransitionReason
from orchestrator.telemetry_wiring import emit_task_span

from .. import clients
from ..core import logger
from ..lineage import resolve_lineage_result
from .approval import (
    REAL_NEWSLETTER_FUNCTION_ID,
    REAL_PUBLISH_ACTION_CLASS,
    REAL_PUBLISH_FUNCTION_ID,
    _approval_card_fields,
)

# ---------------------------------------------------------------------
# Friday: schedule-social-buffer, publish-newsletter -- both request a
# REAL gate-check (mirroring request_approval_handler exactly). Neither
# ever calls Buffer or sends an email itself -- see request_approval_
# handler's own docstring for why, and this module's docstring for
# publish-newsletter's specific caveat.
#
# ROUND 34 (docs/content-learnings.md): both are now per-draft tasks --
# one friday-schedule-social-buffer-* per eligible draft type, gated on
# that ONE draft's own 2 Thursday review tasks via resolve_lineage_result,
# same pattern as request_approval_handler. case-study deliberately has
# no friday-schedule-social-buffer-* task at all -- see
# draft_case_study_handler's docstring and function 47's own prompt.md.
# There is no more batch/weekly-cap concept at this granularity (4 draft
# types x 1 post each is already well under the old buffer_weekly_post_
# cap=8) -- each task requests exactly one gate-check for its own draft.
# ---------------------------------------------------------------------

def _complete_nothing_to_publish(
    task_id: str, db: Any, *, task_name: str, ancestor_ref: dict[str, Any]
) -> bool:
    """Friday's counterpart to _single_draft_qa_review's undrafted branch.

    A draft that was deliberately never written (no executive configured,
    no cleared engagement, no proof points this week) reaches Friday with
    a QA-gate result_ref carrying a `status` and no content_hash. Without
    this, schedule-social-buffer and publish-newsletter dead-letter on
    that missing hash every single week -- a permanent red mark standing
    for a gap that is already recorded, three tasks upstream, on the
    drafting task's own result_ref.

    Deliberately keyed on the explicit status marker and nothing else: a
    QA gate that passed a real draft but somehow carries no content_hash
    is still the dead letter it has always been."""
    status = ancestor_ref.get("status")
    if not status or ancestor_ref.get("content_hash"):
        return False
    log_event(
        logger,
        logging.INFO,
        "nothing_to_publish",
        task_id=task_id,
        task_name=task_name,
        status=status,
        draft_task_type=ancestor_ref.get("draft_task_type"),
    )
    db.set_result_ref(
        task_id,
        {
            "status": status,
            "published": False,
            "reason": ancestor_ref.get("reason"),
            "draft_task_type": ancestor_ref.get("draft_task_type"),
        },
    )
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)
    return True


def schedule_social_buffer_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    lineage = resolve_lineage_result(task_id, db)
    if lineage is None:
        raise DispatchError("schedule-social-buffer: no QA-gate ancestor carries a result_ref")
    _ancestor_task, ancestor_ref = lineage
    if _complete_nothing_to_publish(
        task_id, db, task_name="schedule-social-buffer", ancestor_ref=ancestor_ref
    ):
        return
    content_hash = ancestor_ref.get("content_hash")
    if not content_hash:
        raise DispatchError(
            "schedule-social-buffer: QA-gate ancestor result_ref carries no content_hash"
        )
    draft_task_type = ancestor_ref.get("draft_task_type")
    draft_task_id = ancestor_ref.get("draft_task_id")
    # ROUND 34 (10 Aug 2026): gate_decisions.agent_run_id is a NOT NULL FK
    # to agent_runs (contracts/vault-schema/schema.sql) -- envelope.
    # agent_run_id is a synthetic uuid5 the worker computes for tracing
    # only and no handler ever inserts into agent_runs, so it always 500s
    # with ForeignKeyViolation on a real gate-check call. Use the Thursday
    # QA-gate ancestor's own agent_run_id instead -- a real row
    # _single_draft_qa_review already created via vault.create_agent_run.
    # See request_approval_handler's docstring for the full incident.
    approving_agent_run_id = ancestor_ref.get("agent_run_id")
    if not approving_agent_run_id:
        raise DispatchError(
            "schedule-social-buffer: QA-gate ancestor result_ref carries no agent_run_id"
        )

    with clients.build_vault_client() as vault:
        card = _approval_card_fields(task_id, db, vault, ancestor_ref)

    with clients.build_gatekeeper_client() as gatekeeper:
        with emit_task_span(
            "schedule-social-buffer",
            function_id=REAL_PUBLISH_FUNCTION_ID,
            task_ref=task_id,
            model="none",
            cost=0.0,
            run_id=str(envelope.campaign_id),
        ):
            decision = gatekeeper.gate_check(
                agent_run_id=str(approving_agent_run_id),
                function_id=REAL_PUBLISH_FUNCTION_ID,
                action_class=REAL_PUBLISH_ACTION_CLASS,
                content_hash=content_hash,
                preview_title=card["preview_title"],
                preview_reference=f"weekly-content-loop://{task_id}",
                evidence_summary=card["evidence_summary"],
                subject=card["subject"],
            )

    db.set_result_ref(
        task_id,
        {
            "draft_task_id": draft_task_id,
            "draft_task_type": draft_task_type,
            "decision_id": decision.get("decision_id"),
            "outcome": decision.get("outcome"),
            "approval_id": decision.get("approval_id"),
            "content_hash": content_hash,
            # Process 7. The publish step runs on a separate loop, long
            # after this run has ended, and resolves nothing by lineage --
            # it finds this row by query. Everything it needs to ask "was
            # this approved, and what exactly do I publish" therefore has
            # to be ON this row: the identity the approval was raised
            # under, the policy key it was raised against, the subject the
            # gate token will carry, and the asset whose bytes are bound
            # to content_hash above.
            "agent_run_id": str(approving_agent_run_id),
            "function_id": REAL_PUBLISH_FUNCTION_ID,
            "subject": card["subject"],
            "vault_asset_id": ancestor_ref.get("vault_asset_id"),
            # Process 8. The slug the published links actually carry, and
            # the campaign it belongs to -- the pair the publish sweep
            # registers in analytics.utm_campaign_map so the nightly
            # ingest can attribute metrics to this week's content instead
            # of quarantining them.
            "campaign": ancestor_ref.get("campaign"),
            "campaign_id": ancestor_ref.get("campaign_id"),
        },
    )
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)

def publish_newsletter_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    """Requests approval only -- see module docstring: no real email will
    send once approved until Publisher's Graph integration is wired with
    a real Entra ID app registration Pieter still needs to create.

    ROUND 34: gated on draft-newsletter's own 2 Thursday review tasks only
    (not all 12), resolved via resolve_lineage_result exactly like
    schedule-social-buffer -- no more searching sibling drafts for "the
    newsletter one", since this task's own dependency graph already
    guarantees its single ancestor IS the newsletter draft."""
    lineage = resolve_lineage_result(task_id, db)
    if lineage is None:
        raise DispatchError("publish-newsletter: no QA-gate ancestor carries a result_ref")
    _ancestor_task, ancestor_ref = lineage
    if _complete_nothing_to_publish(
        task_id, db, task_name="publish-newsletter", ancestor_ref=ancestor_ref
    ):
        return
    content_hash = ancestor_ref.get("content_hash")
    if not content_hash:
        raise DispatchError(
            "publish-newsletter: QA-gate ancestor result_ref carries no content_hash"
        )
    draft_task_id = ancestor_ref.get("draft_task_id")
    # ROUND 34 (10 Aug 2026): see schedule_social_buffer_handler's comment
    # / request_approval_handler's docstring -- envelope.agent_run_id is
    # never a real agent_runs row, so gate-check always 500s on the FK.
    approving_agent_run_id = ancestor_ref.get("agent_run_id")
    if not approving_agent_run_id:
        raise DispatchError(
            "publish-newsletter: QA-gate ancestor result_ref carries no agent_run_id"
        )

    with clients.build_vault_client() as vault:
        # The newsletter's own caveat is preserved in front of the
        # generated title: "send NOT yet wired" is the single most
        # important thing an approver of this card needs to know, and it
        # is not derivable from the lineage.
        card = _approval_card_fields(
            task_id, db, vault, ancestor_ref, prefix="[NEWSLETTER — send NOT yet wired]"
        )

    with clients.build_gatekeeper_client() as gatekeeper:
        with emit_task_span(
            "publish-newsletter",
            function_id=REAL_NEWSLETTER_FUNCTION_ID,
            task_ref=task_id,
            model="none",
            cost=0.0,
            run_id=str(envelope.campaign_id),
        ):
            decision = gatekeeper.gate_check(
                agent_run_id=str(approving_agent_run_id),
                function_id=REAL_NEWSLETTER_FUNCTION_ID,
                action_class=REAL_PUBLISH_ACTION_CLASS,
                content_hash=content_hash,
                preview_title=card["preview_title"],
                preview_reference=f"weekly-content-loop://{draft_task_id or task_id}",
                evidence_summary=card["evidence_summary"],
                subject=card["subject"],
            )

    db.set_result_ref(
        task_id,
        {
            "decision_id": decision.get("decision_id"),
            "outcome": decision.get("outcome"),
            "approval_id": decision.get("approval_id"),
            "content_hash": content_hash,
            # Same reasoning as schedule-social-buffer above. Note this
            # row is found by the publish loop and then declined: there is
            # no ESP send path (see publish_approved_assets_handler).
            "agent_run_id": str(approving_agent_run_id),
            "function_id": REAL_NEWSLETTER_FUNCTION_ID,
            "subject": card["subject"],
            "vault_asset_id": ancestor_ref.get("vault_asset_id"),
            "campaign": ancestor_ref.get("campaign"),
            "campaign_id": ancestor_ref.get("campaign_id"),
            "draft_task_id": draft_task_id,
            "draft_task_type": ancestor_ref.get("draft_task_type"),
        },
    )
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)

