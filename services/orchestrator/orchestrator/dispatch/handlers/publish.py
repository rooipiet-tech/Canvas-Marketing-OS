from __future__ import annotations

import logging
from typing import Any

from orchestrator.logging_config import log_event, sanitize_exception_text
from orchestrator.models import TaskEnvelope, TaskStateEnum, TransitionReason

from .. import clients
from ..core import logger
from .approval import REAL_PUBLISH_ACTION_CLASS, REAL_PUBLISH_FUNCTION_ID

# =======================================================================
# PROCESS 7 -- publish. The step that did not exist.
# =======================================================================
#
# Both loops terminated at the approval request. request-approval,
# schedule-social-buffer and publish-newsletter each raise an approval
# card and complete, and NO task in either graph depends on any of them.
# ca-publisher -- a deployed service with a real Buffer path, a gate-token
# verifier and a JTI ledger -- was never called by anything: the
# orchestrator had no publisher client at all. The pipeline's last act was
# to ask a human, and nothing consumed the answer.
#
# WHY THIS IS A SEPARATE LOOP, not a task in the loop that asked.
# request-approval "completes as soon as /gate-check responds -- never
# waits/polls on the human decision (AC-01's bounded scope)". By the time
# anyone clicks Approve, that run is finished. A publish task inside the
# same graph would find "still pending" essentially always. Running on its
# own heartbeat is also what makes an approval granted three days late
# still publish, with no coupling between the governance click surface and
# the orchestrator.
#
# HOW THE TOKEN IS OBTAINED. /gate-check at level 1 with no prior approval
# escalates and returns NO gate token. The second call, after a human has
# approved that exact (agent_run_id, function_id, content_hash) triple,
# finds the approved row via latest_approved() and issues one. So this
# handler calls /gate-check a second time -- that IS the mint step, not a
# redundant policy re-evaluation, and it is also what re-checks the kill
# switch and the policy at publish time rather than trusting a decision
# made when the approval was raised.
#
# WHAT IS DELIBERATELY NOT DONE HERE:
#   * PUBLISHER_DRY_RUN is left alone. It defaults to true and is set
#     nowhere in infra, so the deployed publisher performs no live call.
#     Wiring the publish step and flipping the system to live posting are
#     two separate decisions and this is only the first.
#   * The newsletter is found and declined. app/esp_client.py is a
#     complete, tested Mailchimp/MailerLite adapter that nothing imports
#     except its own test, POST /publish has no ESP branch at all, and no
#     API key, list id or from-address exists in infra. Publishing it
#     would mean building against credentials that do not exist. Declining
#     visibly, on the row, is the honest outcome.

PUBLISH_CANDIDATE_TASK_TYPES = ["schedule-social-buffer", "publish-newsletter"]

# Only the social path has a publisher route. publish.blog_article (the
# newsletter) reaches POST /publish's Buffer branch, which would post an
# email digest to LinkedIn -- so it is filtered out here rather than sent
# to the wrong channel.
PUBLISHABLE_FUNCTION_IDS = {REAL_PUBLISH_FUNCTION_ID}

PUBLISH_STATUS_APPROVED = "approved"

# analytics.scheduled_posts' channel vocabulary is analytics_ingest.rollup's
# own _CHANNEL_TABLE: {"buffer": buffer_post_metrics, "linkedin":
# linkedin_metrics}. The publisher's only live route is Buffer's
# create_draft, so "buffer" is the channel whose observed rows form the
# numerator this denominator is divided into.
PUBLISH_CHANNEL = "buffer"


def _publish_one(
    task: dict[str, Any],
    db: Any,
    vault: Any,
    gatekeeper: Any,
    publisher: Any,
) -> dict[str, Any]:
    """Publish one approved asset, or record why it was not published.

    Returns a summary row; never raises for a single asset's own problem.
    One rejected approval, one unreachable asset or one publisher refusal
    must not stop the other assets in the same sweep from publishing --
    the same reasoning the weekly loop's per-draft gating was rebuilt
    around in round 34.
    """
    ref = task.get("result_ref") or {}
    task_id = task["task_id"]
    outcome: dict[str, Any] = {"task_id": task_id, "task_type": task.get("task_type")}

    function_id = ref.get("function_id")
    agent_run_id = ref.get("agent_run_id")
    content_hash = ref.get("content_hash")
    if not (function_id and agent_run_id and content_hash):
        return {**outcome, "status": "incomplete_result_ref"}

    status = gatekeeper.get_approval_status(
        agent_run_id=agent_run_id, function_id=function_id, content_hash=content_hash
    )
    decision = status.get("status")
    if decision != PUBLISH_STATUS_APPROVED:
        # pending / rejected / expired / not_found are all ordinary, and
        # none of them is this sweep's problem to solve. A pending row is
        # picked up by the next heartbeat; a rejected one never publishes.
        return {**outcome, "status": f"not_approved:{decision}"}

    if function_id not in PUBLISHABLE_FUNCTION_IDS:
        return {**outcome, "status": "no_publish_route", "function_id": function_id}

    vault_asset_id = ref.get("vault_asset_id")
    if not vault_asset_id:
        return {**outcome, "status": "no_vault_asset_id"}
    asset = vault.get_asset(vault_asset_id)
    asset_bytes_b64 = asset["content_base64"]

    # The mint call. Publisher recomputes the hash of the bytes it is sent
    # and compares it with the hash bound into this token, so a token
    # minted for one asset cannot publish another.
    minted = gatekeeper.gate_check(
        agent_run_id=agent_run_id,
        function_id=function_id,
        action_class=REAL_PUBLISH_ACTION_CLASS,
        content_hash=content_hash,
        subject=ref.get("subject"),
    )
    gate_token = minted.get("gate_token")
    if not gate_token:
        # The policy re-evaluated to something other than "approved" at
        # publish time -- the kill switch, a policy edit, or an approval
        # that expired between the status read above and this call.
        return {**outcome, "status": "no_gate_token", "outcome_reported": minted.get("outcome")}

    result = publisher.publish(
        agent_run_id=agent_run_id,
        function_id=function_id,
        asset_bytes_b64=asset_bytes_b64,
        gate_token=gate_token,
        asset_id=vault_asset_id,
    )

    # Written back onto the approving task's own row, which is what
    # find_awaiting_publication() filters on -- this is what stops the
    # next heartbeat re-publishing the same asset.
    # Process 8. Publication is the one moment the campaign slug, the
    # campaign and the asset are all known together, so it is the only
    # place these two lookups can honestly be written. Neither is allowed
    # to fail a publish that has already happened -- the post is live, and
    # a missing map row is a measurement gap the next publish under the
    # same slug repairs, whereas a raise here would leave a published
    # asset marked unpublished and republish it on the next sweep.
    campaign_slug = ref.get("campaign")
    try:
        db.register_utm_campaign(campaign_slug, ref.get("campaign_id"), vault_asset_id)
        db.record_scheduled_post(PUBLISH_CHANNEL)
    except Exception as exc:  # noqa: BLE001 - see comment above
        log_event(
            logger,
            logging.WARNING,
            "analytics_registration_failed",
            task_id=task_id,
            utm_campaign=campaign_slug,
            error=sanitize_exception_text(exc),
        )

    db.set_result_ref(
        task_id,
        {
            **ref,
            "publish_attempt_id": result.get("attempt_id"),
            "publish_status": result.get("status"),
            "publish_reason": result.get("reason"),
            "utm_campaign_registered": bool(campaign_slug),
        },
    )
    return {
        **outcome,
        "status": "published",
        "publish_status": result.get("status"),
        "publish_reason": result.get("reason"),
    }


def publish_approved_assets_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    """Publishes every approved-but-unpublished asset, one sweep."""
    candidates = db.find_awaiting_publication(PUBLISH_CANDIDATE_TASK_TYPES)
    results: list[dict[str, Any]] = []

    if candidates:
        with clients.build_vault_client() as vault:
            with clients.build_gatekeeper_client() as gatekeeper:
                with clients.build_publisher_client() as publisher:
                    for candidate in candidates:
                        try:
                            results.append(
                                _publish_one(candidate, db, vault, gatekeeper, publisher)
                            )
                        except Exception as exc:  # noqa: BLE001 - see _publish_one's docstring
                            log_event(
                                logger,
                                logging.ERROR,
                                "publish_asset_failed",
                                task_id=candidate.get("task_id"),
                                error=sanitize_exception_text(exc),
                            )
                            results.append(
                                {
                                    "task_id": candidate.get("task_id"),
                                    "task_type": candidate.get("task_type"),
                                    "status": "error",
                                }
                            )

    published = [row for row in results if row.get("status") == "published"]
    log_event(
        logger,
        logging.INFO,
        "publish_sweep_completed",
        candidates=len(candidates),
        published=len(published),
    )
    db.set_result_ref(
        task_id,
        {
            "candidates": len(candidates),
            "published": len(published),
            "results": results,
        },
    )
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)


