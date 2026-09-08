from __future__ import annotations

import base64
import difflib
import json
import logging
import re
from typing import Any

import yaml
from telemetry_lib import set_span_attribute

from orchestrator import brand_rules
from orchestrator.clients.gateway_client import OrchestratorGatewayClient
from orchestrator.clients.vault_client_ext import VaultClientExt
from orchestrator.config import policies_dir
from orchestrator.dispatch_errors import DispatchError
from orchestrator.logging_config import log_event
from orchestrator.models import TaskEnvelope, TaskStateEnum, TransitionReason
from orchestrator.telemetry_wiring import emit_task_span

from .. import clients
from ..completion import _complete_and_meter
from ..core import (
    FUNCTION_ID_02,
    FUNCTION_ID_39,
    FUNCTION_ID_43,
    FUNCTION_ID_45,
    FUNCTION_ID_46,
    FUNCTION_ID_47,
    FUNCTION_ID_48_FACT_CHECK,
    _agent_name,
    _campaign_name,
    _now_iso,
    _parse_json_content,
    _read_prompt,
    _validate_function_input,
    logger,
)
from ..lineage import resolve_lineage_result
from ..qa_common import (
    QA_VERDICT_UNSPECIFIED_FAILURE,
    _resolve_verdict,
    _review_channel,
    _reviewable_draft_text,
    _teams_display_text,
)
from .canva import _render_carousel
from .drafting import _render_simple_post
from .misc_drafts import _render_case_study, _render_newsletter
from .research_brief import _carried_brief_fields

# ---------------------------------------------------------------------
# Thursday per-draft QA gates (qa-review-brand-steward, qa-review-fact-
# check) -- see module docstring for why these are NOT a generalisation
# of qa_review_handler despite the structural similarity.
# ---------------------------------------------------------------------

# Round 34 (docs/content-learnings.md): weekly-content-loop.yaml now has
# ONE Thursday review task per Wednesday draft per review_kind (12 total,
# was 2 aggregate tasks covering all 6 drafts each). This replaces the old
# _aggregate_qa_review / QA_REVIEWED_DRAFT_TASK_TYPES / _gather_sibling_
# drafts machinery, which reviewed all 6 drafts inside ONE task and
# resolved that task to a single all-or-nothing terminal state -- so one
# bad draft (a spelling typo, say) dead-lettered friday-schedule-social-
# buffer and friday-publish-newsletter for every OTHER draft too, even
# ones that individually passed both reviews cleanly. See the round-34
# "batch-gating" finding in docs/content-learnings.md for the full
# incident history (confirmed live: 4-5 of 6 drafts individually clean,
# both Friday tasks cascade-dead-lettered anyway on the other 1-2).

# ---------------------------------------------------------------------
# F-QA-RETRY-LOOP (11 Aug 2026) -- reopened qa-feedback-loop-proposal-
# 2026-08-05.md per Pieter's explicit redesign (see
# claude_qa-feedback-loop-proposal-b-v2-2026-08-11.md for the full spec
# this implements). The non-negotiable invariant, stated plainly: every
# one of the 6 Wednesday drafts must reach Teams every night, whether it
# passed QA cleanly, passed after an automated retry, or exhausted every
# retry -- content-quality safeguards (the anti-hollowing check below)
# must NEVER be the reason a draft goes missing from Teams. Phase 1
# (bounded regenerate-and-recheck) and Phase 2a (a "retries exhausted"
# Teams card carrying a track-changes diff) are implemented here. Phase
# 2b (a human's Teams comment triggering exactly one more retry) and
# Phase 3 (routing an unresolved draft into governance.approval_requests)
# are DELIBERATELY NOT implemented in this change -- both need a new
# inbound API surface (something to receive a Teams Action.Submit /
# adaptive-card-input callback) that does not exist anywhere in this
# codebase yet, and are scoped out explicitly rather than half-built. See
# this PR's description for the follow-up.
# ---------------------------------------------------------------------

MAX_QA_RETRY_ATTEMPTS = 10

# Per-review-kind identity, mirroring qa_review_brand_steward_handler /
# qa_review_fact_check_handler's own kwargs to _single_draft_qa_review --
# duplicated here (not imported from those two thin wrappers, which pass
# these as call-site literals rather than a lookup table) so the retry
# loop can run EITHER review kind against a regenerated draft without
# needing a live TaskEnvelope/task_id for the kind it didn't start from.
_QA_REVIEW_PARAMS: dict[str, dict[str, str]] = {
    "brand_steward": {
        "task_type": "qa-review-brand-steward",
        "function_id": FUNCTION_ID_02,
        "prompt_dir": "02-brand-steward-qa",
        "agent_name": "brand-steward-qa",
    },
    "fact_check": {
        "task_type": "qa-review-fact-check",
        "function_id": FUNCTION_ID_48_FACT_CHECK,
        "prompt_dir": "48-fact-check-verdict",
        "agent_name": "fact-check-verdict",
    },
}

# Regeneration recipe per Wednesday draft task_type -- one entry per
# _draft_social_post_handler-routed drafting handler (see each one's own
# definition above for where these values come from). draft-content-
# repurpose is DELIBERATELY excluded: it has its own dedicated handler
# with a different lineage mechanism (two source-draft depends_on
# entries, not a research-brief walk -- see _select_repurpose_source's
# docstring), and teaching the retry loop that second shape is scoped
# out of this change. A draft type absent from this table falls back to
# the pre-retry-loop, single-shot behaviour untouched -- see
# _single_draft_qa_review's own not-passed branch below.
_DRAFT_REGEN_PARAMS: dict[str, dict[str, Any]] = {
    "draft-insight-to-story": {
        "function_id": FUNCTION_ID_39,
        "prompt_dir": "39-insight-to-story-editor",
        "agent_name": "insight-to-story-editor",
        "asset_type": "linkedin_post",
        "render_draft_text": _render_simple_post,
        "max_tokens": 2048,
    },
    "draft-executive-ghostwrite": {
        "function_id": FUNCTION_ID_43,
        "prompt_dir": "43-executive-ghostwriter",
        "agent_name": "executive-ghostwriter",
        "asset_type": "linkedin_post",
        "render_draft_text": _render_simple_post,
        "max_tokens": 2560,
    },
    "draft-carousel-post": {
        "function_id": FUNCTION_ID_45,
        "prompt_dir": "45-carousel-post-writer",
        "agent_name": "carousel-post-writer",
        "asset_type": "carousel_post",
        "render_draft_text": _render_carousel,
        "max_tokens": 2560,
    },
    "draft-newsletter": {
        "function_id": FUNCTION_ID_46,
        "prompt_dir": "46-newsletter-writer",
        "agent_name": "newsletter-writer",
        "asset_type": "newsletter",
        "render_draft_text": _render_newsletter,
        "max_tokens": 3584,
    },
    "draft-case-study": {
        "function_id": FUNCTION_ID_47,
        "prompt_dir": "47-case-study-writer",
        "agent_name": "case-study-writer",
        "asset_type": "case_study",
        "render_draft_text": _render_case_study,
        "max_tokens": 4096,
    },
}


def _looks_hollowed(original: str, revised: str) -> bool:
    """Best-effort, NON-BLOCKING signal only (Pieter's explicit 11 Aug
    2026 ruling: "if deletion is better than 10 retries deletion is
    better" -- i.e. this NEVER gates or stops a retry attempt; it only
    gets logged and surfaced on the retries-exhausted Teams card so a
    human reviewer knows to look closely at what changed). Flags a
    revision that dropped the canvasintelligence.com URL the original
    carried, shrank by more than ~40%, or lost every digit the original
    had (a crude proxy for "lost its proof points") -- any one of which
    is a plausible sign a retry attempt fixed a QA violation by deleting
    content rather than rewriting it."""
    if "canvasintelligence.com" in original and "canvasintelligence.com" not in revised:
        return True
    if len(original) > 40 and len(revised) < len(original) * 0.6:
        return True
    if re.search(r"\d", original) and not re.search(r"\d", revised):
        return True
    return False


def _finalize_qa_failure(
    db: Any,
    task_id: str,
    *,
    review_kind: str,
    violations: list[str],
    draft_task: dict[str, Any],
    draft_text: str,
    agent_run_id: str | None = None,
    campaign_id: str | None = None,
) -> None:
    """The pre-retry-loop single-shot failure outcome (set_result_ref +
    FAILED/QA_BLOCKED + notify_needs_edit), extracted unchanged from
    _single_draft_qa_review's own not-passed branch so it can be reused
    by every path that ends in this one outcome: a NEVER_RETRYABLE
    violation, a draft_task_type the retry loop doesn't know how to
    regenerate, and a task that lost the sibling-ownership race for its
    draft's advisory lock."""
    db.set_result_ref(
        task_id,
        {
            "pass": False,
            "violations": violations,
            "draft_task_id": draft_task["task_id"],
            "draft_task_type": draft_task.get("task_type"),
            "agent_run_id": agent_run_id,
            "campaign_id": campaign_id,
        },
    )
    db.transition(task_id, TaskStateEnum.FAILED, TransitionReason.QA_BLOCKED)
    log_event(
        logger,
        logging.INFO,
        "qa_review_blocked",
        task_id=task_id,
        review_kind=review_kind,
        draft_task_id=draft_task["task_id"],
        violations=violations,
    )
    from orchestrator import teams_notify

    teams_notify.notify_needs_edit(
        task_id=task_id,
        channel="linkedin",
        violations=violations,
        draft_excerpt=_teams_display_text(draft_text)[:280],
    )


def _regenerate_draft_content(
    *,
    task_id: str,
    db: Any,
    vault: VaultClientExt,
    gateway: OrchestratorGatewayClient,
    envelope: TaskEnvelope,
    campaign_id: str,
    function_id: str,
    prompt_dir: str,
    agent_name: str,
    render_draft_text: Any,
    max_tokens: int,
    brief_body: Any,
    pillar: Any,
    vertical: Any,
    audience_note: Any,
    previous_draft_text: str,
    violations: list[str],
    attempt: int,
) -> tuple[str, dict[str, Any]]:
    """One regeneration attempt for the QA retry loop. Same completion
    shape _draft_social_post_handler uses to draft the first time, plus a
    `revision_feedback` field naming exactly which QA violations to fix
    and an explicit anti-hollowing instruction. Sent as DATA (part of
    user_content), not a prompt.md edit -- a per-attempt violation list
    is per-attempt data, not a static policy the prompt file should
    hardcode.

    Deliberately has NO side effect on task_state: unlike
    _draft_social_post_handler (which both drafts AND completes its own
    task), this only produces text -- the caller owns creating the Vault
    asset and deciding what happens to the draft task's result_ref, since
    a retry attempt must never itself fire advance_dependents."""
    agent_run = vault.create_agent_run_idempotent(
        task_id=task_id,
        db=db,
        agent_name=_agent_name(agent_name, envelope),
        campaign_id=campaign_id,
        function_id=function_id,
        status="running",
        input_payload={
            "pillar": pillar,
            "retry_attempt": attempt,
            "revision_violations": violations,
        },
    )
    system_prompt = _read_prompt(prompt_dir)
    user_content = json.dumps(
        {
            "brief": brief_body,
            "pillar": pillar,
            "vertical": vertical,
            "audience_note": audience_note,
            "revision_feedback": {
                "previous_draft": previous_draft_text,
                "violations_to_fix": violations,
                "instruction": (
                    "This is a revision, not a new draft. The previous draft above "
                    "failed QA review on exactly the violations listed. Fix only "
                    "those specific issues. Do not remove the call to action, do "
                    "not remove or alter the canvasintelligence.com URL, do not "
                    "drop any proof point, statistic, or named product/partner "
                    "reference that was not itself flagged. Keep the output "
                    "approximately the same length and shape as the previous draft "
                    "unless a flagged violation requires otherwise."
                ),
            },
        }
    )
    response, cost = _complete_and_meter(
        gateway,
        vault,
        model="claude-sonnet",
        system_prompt=system_prompt,
        user_content=user_content,
        agent_run_id=agent_run["id"],
        content_class="public_source_content",
        max_tokens=max_tokens,
    )
    output = _parse_json_content(response["content"])
    draft_text = render_draft_text(output)
    vault.update_agent_run(
        agent_run["id"], status="succeeded", output_payload=output, completed_at=_now_iso()
    )
    return draft_text, agent_run


def _run_single_qa_check(
    *,
    task_id: str,
    db: Any,
    vault: VaultClientExt,
    gateway: OrchestratorGatewayClient,
    envelope: TaskEnvelope,
    campaign_id: str,
    draft_text: str,
    review_kind: str,
    permission_check_module: Any,
) -> tuple[list[str], str]:
    """Runs ONE QA completion (brand_steward or fact_check) against
    draft_text and returns (reconciled_violations, agent_run_id) --
    shares _single_draft_qa_review's exact verdict/reconciliation logic
    (permission_check + brand_rules.reconcile_violations) so a retry
    attempt is graded by the identical rule set an ordinary Thursday
    review uses, never a looser or different check."""
    params = _QA_REVIEW_PARAMS[review_kind]
    system_prompt = _read_prompt(params["prompt_dir"])
    agent_run = vault.create_agent_run_idempotent(
        task_id=task_id,
        db=db,
        agent_name=_agent_name(params["agent_name"], envelope),
        campaign_id=campaign_id,
        function_id=params["function_id"],
        status="running",
        input_payload={"channel": "linkedin", "review_kind": review_kind},
    )
    user_content = json.dumps(
        {"draft_text": draft_text, "client_references": [], "channel": "linkedin"}
    )
    response, cost = _complete_and_meter(
        gateway,
        vault,
        model="claude-sonnet",
        system_prompt=system_prompt,
        user_content=user_content,
        agent_run_id=agent_run["id"],
        content_class="public_source_content",
    )
    verdict = _parse_json_content(response["content"])
    violations = list(verdict.get("violations") or [])
    uncleared = permission_check_module.find_uncleared_references([])
    if uncleared and permission_check_module.VIOLATION_CODE not in violations:
        violations.append(permission_check_module.VIOLATION_CODE)
    violations, dropped = brand_rules.reconcile_violations(violations, draft_text)
    if dropped:
        log_event(
            logger,
            logging.WARNING,
            "qa_review_false_positive_dropped",
            review_kind=review_kind,
            dropped_violations=dropped,
        )
    vault.update_agent_run(
        agent_run["id"],
        status="succeeded" if not violations else "failed",
        output_payload={"pass": not violations, "violations": violations},
        completed_at=_now_iso(),
    )
    return violations, agent_run["id"]


def _run_qa_retry_loop(
    task_id: str,
    envelope: TaskEnvelope,
    db: Any,
    *,
    review_kind: str,
    draft_task: dict[str, Any],
    initial_violations: list[str],
    initial_draft_text: str,
    permission_check_module: Any,
) -> None:
    """Owns up to MAX_QA_RETRY_ATTEMPTS regenerate-and-recheck attempts
    for ONE draft, on behalf of BOTH per-draft QA review tasks (brand_
    steward AND fact_check) at once -- never just the review kind that
    happened to detect the first violation. This function only ever runs
    while holding the draft's advisory lock (see db.try_advisory_lock and
    this module's _single_draft_qa_review caller), which is what makes it
    safe to be the sole place that regenerates content or finalizes
    either sibling task's terminal state -- see
    claude_qa-block-retry-investigation-2026-08-11.md for the two-
    independent-task race this avoids.

    Every attempt re-runs BOTH review kinds against the newly regenerated
    draft, not just the one this loop happened to be entered for -- a fix
    aimed at a fact_check violation could accidentally introduce a brand_
    steward one, and vice versa; only a joint pass on both counts as
    success. Caller (_single_draft_qa_review) has already confirmed the
    draft's task_type has a regeneration recipe (_DRAFT_REGEN_PARAMS) and
    that none of the initial violations are NEVER_RETRYABLE."""
    draft_task_id = draft_task["task_id"]
    draft_task_type = draft_task.get("task_type")
    regen_params = _DRAFT_REGEN_PARAMS[draft_task_type]
    other_review_kind = "fact_check" if review_kind == "brand_steward" else "brand_steward"

    siblings = db.find_dependent_tasks(draft_task_id)
    other_task_type = _QA_REVIEW_PARAMS[other_review_kind]["task_type"]
    sibling_row = next((row for row in siblings if row["task_type"] == other_task_type), None)

    task_ids_by_kind = {review_kind: task_id}
    if sibling_row is not None:
        task_ids_by_kind[other_review_kind] = sibling_row["task_id"]

    # TD-35: this loop re-runs BOTH review kinds on every regeneration
    # attempt regardless of which sibling task triggered it, so a
    # brand_steward failure alone is enough to reach the fact_check
    # sub-check below even while qa_review_fact_check_handler's own
    # entry-point gate would have refused to run at all. never_retryable
    # carries the gate's violation code so a disabled gate is discovered
    # once (attempt 1's fresh fact_check result) and then stops the loop
    # from burning further regeneration attempts it can never pass.
    never_retryable = {permission_check_module.VIOLATION_CODE, FACT_CHECK_GATE_NOT_APPROVED}

    with clients.build_vault_client() as vault, clients.build_gateway_client() as gateway:
        lineage = resolve_lineage_result(draft_task_id, db)
        if lineage is None:
            log_event(
                logger,
                logging.WARNING,
                "qa_retry_loop_no_brief_ancestor",
                draft_task_id=draft_task_id,
            )
            _finalize_qa_failure(
                db,
                task_id,
                review_kind=review_kind,
                violations=initial_violations,
                draft_task=draft_task,
                draft_text=initial_draft_text,
            )
            return
        _ancestor_task, ancestor_ref = lineage
        brief_id = ancestor_ref.get("brief_id")
        brief_body = vault.get_brief(brief_id).get("body") if brief_id else None
        pillar = ancestor_ref.get("pillar")
        vertical = ancestor_ref.get("vertical")
        audience_note = ancestor_ref.get("audience_note")
        campaign_id = vault.get_or_create_campaign(
            _campaign_name(envelope), function_id=regen_params["function_id"]
        )

        current_draft_text = initial_draft_text
        current_violations = {"brand_steward": [], "fact_check": []}
        current_violations[review_kind] = initial_violations
        hollowed = False
        last_review_agent_run_ids: dict[str, str] = {}

        attempt = 0
        for attempt in range(1, MAX_QA_RETRY_ATTEMPTS + 1):
            revision_targets = sorted(
                set(current_violations["brand_steward"]) | set(current_violations["fact_check"])
            )
            if never_retryable & set(revision_targets):
                break  # a NEVER_RETRYABLE code showed up -- stop, fall to the escalation path below

            revised_text, regen_agent_run = _regenerate_draft_content(
                task_id=task_id,
                db=db,
                vault=vault,
                gateway=gateway,
                envelope=envelope,
                campaign_id=campaign_id,
                function_id=regen_params["function_id"],
                prompt_dir=regen_params["prompt_dir"],
                agent_name=regen_params["agent_name"],
                render_draft_text=regen_params["render_draft_text"],
                max_tokens=regen_params["max_tokens"],
                brief_body=brief_body,
                pillar=pillar,
                vertical=vertical,
                audience_note=audience_note,
                previous_draft_text=current_draft_text,
                violations=revision_targets,
                attempt=attempt,
            )
            if _looks_hollowed(initial_draft_text, revised_text):
                hollowed = True

            asset = vault.create_asset(
                asset_type=regen_params["asset_type"],
                agent_run_id=regen_agent_run["id"],
                campaign_id=campaign_id,
                function_id=regen_params["function_id"],
                content_bytes=revised_text.encode("utf-8"),
                approval_state="draft",
            )
            db.set_result_ref(
                draft_task_id,
                {
                    "vault_asset_id": asset["id"],
                    "content_hash": asset["content_hash"],
                    "agent_run_id": regen_agent_run["id"],
                    "campaign_id": campaign_id,
                    "pillar": pillar,
                    "retry_attempt": attempt,
                },
            )
            current_draft_text = revised_text

            bs_violations, bs_agent_run_id = _run_single_qa_check(
                task_id=task_id,
                db=db,
                vault=vault,
                gateway=gateway,
                envelope=envelope,
                campaign_id=campaign_id,
                draft_text=current_draft_text,
                review_kind="brand_steward",
                permission_check_module=permission_check_module,
            )
            if _fact_check_gate_approved():
                fc_violations, fc_agent_run_id = _run_single_qa_check(
                    task_id=task_id,
                    db=db,
                    vault=vault,
                    gateway=gateway,
                    envelope=envelope,
                    campaign_id=campaign_id,
                    draft_text=current_draft_text,
                    review_kind="fact_check",
                    permission_check_module=permission_check_module,
                )
            else:
                # TD-35: no model call while the gate is disabled -- a
                # regenerated draft can never earn a fact_check pass this
                # way either, matching qa_review_fact_check_handler's own
                # entry-point refusal exactly.
                fc_violations, fc_agent_run_id = [FACT_CHECK_GATE_NOT_APPROVED], None
            current_violations = {"brand_steward": bs_violations, "fact_check": fc_violations}
            last_review_agent_run_ids = {
                "brand_steward": bs_agent_run_id,
                "fact_check": fc_agent_run_id,
            }

            log_event(
                logger,
                logging.INFO,
                "qa_review_retry_attempt",
                draft_task_id=draft_task_id,
                attempt=attempt,
                brand_steward_violations=bs_violations,
                fact_check_violations=fc_violations,
                hollowed=hollowed,
            )

            if not bs_violations and not fc_violations:
                for kind, tid in task_ids_by_kind.items():
                    db.set_result_ref(
                        tid,
                        {
                            "pass": True,
                            "vault_asset_id": asset["id"],
                            "content_hash": asset["content_hash"],
                            "draft_task_id": draft_task_id,
                            "draft_task_type": draft_task_type,
                            "agent_run_id": last_review_agent_run_ids[kind],
                            "campaign_id": campaign_id,
                            "retry_attempts": attempt,
                        },
                    )
                    db.transition(tid, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
                    db.advance_dependents(tid)
                log_event(
                    logger,
                    logging.INFO,
                    "qa_review_retry_succeeded",
                    draft_task_id=draft_task_id,
                    attempts=attempt,
                )
                return

        # Exhausted MAX_QA_RETRY_ATTEMPTS, or a NEVER_RETRYABLE violation
        # appeared mid-loop -- escalate instead of silently dropping this
        # draft. Both sibling tasks get finalized FAILED/QA_BLOCKED (same
        # terminal outcome the pre-retry-loop single-shot path used) and
        # ONE Teams card carries the full track-changes context.
        final_violations = sorted(
            set(current_violations["brand_steward"]) | set(current_violations["fact_check"])
        )
        for kind, tid in task_ids_by_kind.items():
            db.set_result_ref(
                tid,
                {
                    "pass": False,
                    "violations": current_violations[kind],
                    "draft_task_id": draft_task_id,
                    "draft_task_type": draft_task_type,
                    "campaign_id": campaign_id,
                    "retry_attempts": attempt,
                },
            )
            db.transition(tid, TaskStateEnum.FAILED, TransitionReason.QA_BLOCKED)

        initial_display_text = _teams_display_text(initial_draft_text)
        current_display_text = _teams_display_text(current_draft_text)
        diff_text = "\n".join(
            difflib.unified_diff(
                initial_display_text.splitlines(),
                current_display_text.splitlines(),
                fromfile="original",
                tofile=f"after attempt {attempt}",
                lineterm="",
            )
        )
        from orchestrator import teams_notify

        teams_notify.notify_retry_exhausted(
            task_id=task_id,
            draft_task_id=draft_task_id,
            channel="linkedin",
            violations=final_violations,
            original_excerpt=initial_display_text[:280],
            revised_excerpt=current_display_text[:280],
            diff_text=diff_text[:3500],
            attempts=attempt,
            hollowed=hollowed,
        )
        log_event(
            logger,
            logging.INFO,
            "qa_review_retry_exhausted",
            draft_task_id=draft_task_id,
            violations=final_violations,
            hollowed=hollowed,
            attempts=attempt,
        )


# F-QA-CHANNEL-HARDCODED: every one of the six Wednesday drafts was
# reviewed as `channel: "linkedin"`, a literal, including the newsletter
# (email) and the case study (a web/deck asset). Function 02's checks 4
# and 5 only branch on "internal-brief" today, so this mislabel changed
# no verdict -- but it is recorded on the agent_run as the evidence of
# what was reviewed, and the moment any channel-specific rule is added it
# would be applied to the wrong asset. The review record should say what
# was actually reviewed.
FACT_CHECK_GATE_NOT_APPROVED = "fact-check-policy-not-approved"


def _fact_check_gate_approved() -> bool:
    """Reads policies/fact-check-gate.yaml fresh on every call -- same
    re-read-every-time convention as _load_allowlist_rule and friends
    below, so a human flipping the flag takes effect on the next dispatch
    without a service restart. Never caches, and never infers approval
    from anything but this one field."""
    path = policies_dir() / "fact-check-gate.yaml"
    policy = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return bool(policy.get("approved"))


def _block_fact_check_gate_not_approved(
    task_id: str,
    db: Any,
    *,
    draft_task: dict[str, Any],
    campaign_id: str | None,
) -> None:
    """The fail-closed outcome when policies/fact-check-gate.yaml's
    `approved` is false -- same terminal shape _finalize_qa_failure gives
    every other QA_BLOCKED outcome (result_ref + FAILED transition + a
    Teams card), so a disabled gate is exactly as visible as a real
    violation, never a silent skip. Raised before any model call, so
    disabling the gate also stops the Thursday cost this review would
    otherwise incur every week."""
    draft_task_type = draft_task.get("task_type")
    db.set_result_ref(
        task_id,
        {
            "pass": False,
            "violations": [FACT_CHECK_GATE_NOT_APPROVED],
            "draft_task_id": draft_task["task_id"],
            "draft_task_type": draft_task_type,
            "campaign_id": campaign_id,
        },
    )
    db.transition(task_id, TaskStateEnum.FAILED, TransitionReason.QA_BLOCKED)
    log_event(
        logger,
        logging.WARNING,
        "qa_review_fact_check_gate_not_approved",
        task_id=task_id,
        draft_task_id=draft_task["task_id"],
    )
    from orchestrator import teams_notify

    teams_notify.notify_needs_edit(
        task_id=task_id,
        channel=_review_channel(draft_task_type),
        violations=[FACT_CHECK_GATE_NOT_APPROVED],
        draft_excerpt=(
            "Thursday fact-check gate is disabled pending Pieter's actual "
            "sign-off -- see policies/fact-check-gate.yaml and "
            "functions/48-fact-check-verdict/REVIEW-PACKET.md."
        )[:280],
    )


def _single_draft_qa_review(
    task_id: str,
    envelope: TaskEnvelope,
    db: Any,
    *,
    task_name: str,
    function_id: str,
    prompt_dir: str,
    agent_name: str,
    review_kind: str,
) -> None:
    """Shared body for qa_review_brand_steward_handler and
    qa_review_fact_check_handler. Reviews exactly ONE Wednesday draft --
    this task's own single ancestor, resolved via resolve_lineage_result
    -- so this task's terminal state (COMPLETED/FAILED) reflects only
    that one draft's outcome, never a sibling's. On pass, forwards
    vault_asset_id/content_hash into this task's own result_ref (same
    pattern as qa_review_handler) so schedule_social_buffer_handler /
    publish_newsletter_handler can resolve them via a plain
    resolve_lineage_result walk without needing to know about drafts at
    all."""
    permission_check = clients.load_permission_check()
    lineage = resolve_lineage_result(task_id, db)
    if lineage is None:
        raise DispatchError(f"{task_name}: no ancestor draft task carries a result_ref to review")
    draft_task, draft_ref = lineage
    vault_asset_id = draft_ref.get("vault_asset_id")
    draft_task_type = draft_task.get("task_type")

    system_prompt = _read_prompt(prompt_dir)

    with clients.build_vault_client() as vault:
        campaign_id = vault.get_or_create_campaign(
            _campaign_name(envelope), function_id=function_id
        )

        if not vault_asset_id and draft_ref.get("status"):
            # The draft was deliberately not attempted (_complete_undrafted
            # -- no executive configured, no cleared engagement, no proof
            # points this week). There is nothing to review and nothing
            # went wrong, so this reviews clean rather than reporting a QA
            # violation every week for a gap that is already logged and
            # already visible on the draft task's own result_ref. Crying
            # wolf here would train a reader to ignore a real QA_BLOCKED.
            #
            # `pass` is deliberately False-y for publication purposes:
            # schedule/publish resolve a vault_asset_id through this
            # result_ref and find none, so nothing can reach Buffer or
            # email on the strength of a skipped draft.
            db.set_result_ref(
                task_id,
                {
                    "status": draft_ref["status"],
                    "reviewed": False,
                    "reason": draft_ref.get("reason"),
                    "draft_task_id": draft_task["task_id"],
                    "draft_task_type": draft_task_type,
                    "campaign_id": campaign_id,
                },
            )
            db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
            log_event(
                logger,
                logging.INFO,
                "qa_review_skipped_undrafted",
                task_id=task_id,
                review_kind=review_kind,
                draft_task_id=draft_task["task_id"],
                status=draft_ref["status"],
            )
            db.advance_dependents(task_id)
            return

        if not vault_asset_id:
            # The draft dependency completed upstream but never produced a
            # reviewable asset (e.g. it was dead-lettered before writing
            # one) -- treat as a violation rather than silently skipping,
            # since QA_BLOCKED intentionally errs toward blocking. Only
            # reached when the draft left no `status` explaining itself,
            # i.e. an asset genuinely went missing.
            db.set_result_ref(
                task_id,
                {
                    "pass": False,
                    "violations": ["no_reviewable_asset"],
                    "draft_task_id": draft_task["task_id"],
                    "draft_task_type": draft_task_type,
                    "campaign_id": campaign_id,
                },
            )
            db.transition(task_id, TaskStateEnum.FAILED, TransitionReason.QA_BLOCKED)
            log_event(
                logger,
                logging.INFO,
                "qa_review_blocked",
                task_id=task_id,
                review_kind=review_kind,
                draft_task_id=draft_task["task_id"],
                violations=["no_reviewable_asset"],
            )
            return

        if review_kind == "fact_check" and not _fact_check_gate_approved():
            _block_fact_check_gate_not_approved(
                task_id, db, draft_task=draft_task, campaign_id=campaign_id
            )
            return

        asset = vault.get_asset(vault_asset_id)
        draft_text = _reviewable_draft_text(
            base64.b64decode(asset["content_base64"]).decode("utf-8")
        )
        channel = _review_channel(draft_task_type)

        payload: dict[str, Any] = {
            "draft_text": draft_text,
            # The drafting functions all take an optional client_reference
            # and none is ever populated (nothing in the register is
            # CLEARED), so the caller has no names to declare -- the empty
            # list is honest here. What it is NOT is a clearance check;
            # see the find_uncleared_in_text call on the verdict below.
            "client_references": [],
            "channel": channel,
        }
        if review_kind == "fact_check":
            # F-FACT-CHECK-BLIND. Function 48 is asked to confirm "every
            # proof point in every Wednesday draft traces to a cited
            # source" and its own prompt explains the compromise it was
            # written under: "Because you receive only the draft text (not
            # the original research brief's {claim, source} pairs from
            # function 41), you verify every specific, checkable claim
            # against the three closed lists below." Those lists are a
            # snapshot of positioning.md, so any claim sourced from this
            # week's market scan was fabricated by definition -- the
            # better processes 1-4 worked, the more Thursday blocked.
            #
            # #119 and the drafting-contract change carried 41's
            # structured {claim, source} pairs to the drafts; this carries
            # them one hop further, to the check whose stated criterion
            # needs them. Pieter's sign-off, 1 Sep 2026, as function 48's
            # own prompt header requires before its scope moves.
            payload["proof_points"] = draft_ref.get("proof_points") or []
        _validate_function_input(function_id, payload)

        agent_run = vault.create_agent_run_idempotent(
            task_id=task_id,
            db=db,
            agent_name=_agent_name(agent_name, envelope),
            campaign_id=campaign_id,
            function_id=function_id,
            status="running",
            input_payload={"channel": channel, "review_kind": review_kind},
        )
        user_content = json.dumps(payload)
        with emit_task_span(
            task_name,
            function_id=function_id,
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
                    content_class="public_source_content",
                )
            set_span_attribute(span, "cost", cost)

        verdict = _parse_json_content(response["content"])
        declared_pass, violations = _resolve_verdict(function_id, verdict)
        # F-CLEARANCE-CHECK-DEAD: this called find_uncleared_references([])
        # -- a literal empty list, which cannot return anything however the
        # register is configured. On all six Wednesday drafts the only
        # deterministic, non-model clearance check did nothing at all,
        # leaving default-deny client naming resting entirely on the
        # model's own reading of check 1. It now reads the draft.
        uncleared = permission_check.find_uncleared_in_text(draft_text)
        if uncleared and permission_check.VIOLATION_CODE not in violations:
            violations.append(permission_check.VIOLATION_CODE)
            log_event(
                logger,
                logging.WARNING,
                "qa_uncleared_client_reference_found_in_draft",
                task_id=task_id,
                draft_task_id=draft_task["task_id"],
                names=[clearance.name for clearance in uncleared],
            )

        violations, dropped_violations = brand_rules.reconcile_violations(violations, draft_text)
        if dropped_violations:
            log_event(
                logger,
                logging.WARNING,
                "qa_review_false_positive_dropped",
                task_id=task_id,
                draft_task_id=draft_task["task_id"],
                review_kind=review_kind,
                dropped_violations=dropped_violations,
            )

        passed = not violations
        if passed and not declared_pass and not dropped_violations:
            # The model refused the draft but named no code -- the schema
            # forbids the combination, and nothing was reconciled away, so
            # this is a refusal whose reason lives only in `notes`. Err
            # toward blocking, as every other branch of this gate does,
            # and keep the model's own words: they are the only account of
            # why. (When reconcile_violations DID drop something, an empty
            # list is the expected, correct result of overriding a known
            # false positive -- that is not this case.)
            violations = [QA_VERDICT_UNSPECIFIED_FAILURE]
            passed = False
            log_event(
                logger,
                logging.WARNING,
                "qa_verdict_failed_without_violation_code",
                task_id=task_id,
                draft_task_id=draft_task["task_id"],
                review_kind=review_kind,
                # See the identical branch in qa_review_handler: the notes
                # are the only account of why, and are kept in the
                # agent_run row rather than in stdout.
                agent_run_id=agent_run["id"],
            )

        vault.update_agent_run(
            agent_run["id"],
            status="succeeded" if passed else "failed",
            # See qa_review_handler's identical call: `notes` is the model's
            # account of its own verdict, kept in the governed store rather
            # than logged. Additive -- `output` is free-form in the frozen
            # contract.
            output_payload={
                "pass": passed,
                "violations": violations,
                "notes": verdict.get("notes"),
            },
            completed_at=_now_iso(),
        )

    if not passed:
        # F-QA-RETRY-LOOP (11 Aug 2026): before falling back to the
        # pre-existing single-shot failure outcome, try to make this
        # violation go away with a bounded, automated retry loop --
        # unless it's the one violation class that must never be retried
        # (a regeneration attempt could itself fabricate a client
        # reference), or this draft's task_type has no regeneration
        # recipe yet (draft-content-repurpose -- see _DRAFT_REGEN_PARAMS).
        never_retryable = {permission_check.VIOLATION_CODE}
        regen_available = draft_task_type in _DRAFT_REGEN_PARAMS

        if not regen_available:
            log_event(
                logger,
                logging.INFO,
                "qa_retry_loop_not_available_for_draft_type",
                task_id=task_id,
                draft_task_type=draft_task_type,
                draft_task_id=draft_task["task_id"],
            )

        if (never_retryable & set(violations)) or not regen_available:
            _finalize_qa_failure(
                db,
                task_id,
                review_kind=review_kind,
                violations=violations,
                draft_task=draft_task,
                draft_text=draft_text,
                agent_run_id=agent_run["id"],
                campaign_id=campaign_id,
            )
            return  # never advance_dependents -- this draft's own Friday task must never see it

        # A draft's two Thursday review tasks (brand_steward / fact_check)
        # run independently and can both hit a violation at nearly the
        # same moment -- an advisory lock keyed on the draft's own
        # task_id makes whichever one gets here first the sole retry-loop
        # owner (see db.try_advisory_lock's docstring and
        # claude_qa-block-retry-investigation-2026-08-11.md for the race
        # this avoids). The one that loses the race does NOT wait on the
        # other -- it falls straight through to the same single-shot
        # outcome this task always had before this feature existed, so a
        # draft is never left hanging on an in-handler blocking wait. If
        # the owner later succeeds or exhausts its retries, it explicitly
        # re-finalizes BOTH sibling tasks (including this one) with the
        # final outcome -- see _run_qa_retry_loop -- which may mean this
        # task's own "needs edit" card here turns out to be superseded a
        # few seconds later by the owner's resolution. Documented,
        # accepted tradeoff: a possible duplicate/stale-looking Teams
        # notification is far cheaper than a stuck task handler.
        lock_key = db.advisory_lock_key_for(draft_task["task_id"])
        lock_conn = db.try_advisory_lock(lock_key)
        if lock_conn is None:
            log_event(
                logger,
                logging.INFO,
                "qa_retry_loop_deferred_to_sibling",
                task_id=task_id,
                review_kind=review_kind,
                draft_task_id=draft_task["task_id"],
            )
            _finalize_qa_failure(
                db,
                task_id,
                review_kind=review_kind,
                violations=violations,
                draft_task=draft_task,
                draft_text=draft_text,
                agent_run_id=agent_run["id"],
                campaign_id=campaign_id,
            )
            return

        try:
            _run_qa_retry_loop(
                task_id,
                envelope,
                db,
                review_kind=review_kind,
                draft_task=draft_task,
                initial_violations=violations,
                initial_draft_text=draft_text,
                permission_check_module=permission_check,
            )
        finally:
            db.release_advisory_lock(lock_conn)
        return  # never advance_dependents here -- _run_qa_retry_loop already did, on success

    passed_ref: dict[str, Any] = {
        "pass": True,
        "vault_asset_id": vault_asset_id,
        "content_hash": asset.get("content_hash"),
        "draft_task_id": draft_task["task_id"],
        "draft_task_type": draft_task_type,
        "review_kind": review_kind,
        "agent_run_id": agent_run["id"],
        "campaign_id": campaign_id,
    }
    # Friday's approval card is built from whichever ONE of this draft's
    # two Thursday gates resolve_lineage_result happens to stop at, so the
    # pillar, campaign and proof points have to survive this hop or the
    # card has nothing to describe. Same reason the QA gate carries them
    # from the brief to the drafts (F-BRIEF-FIELDS-DROPPED-BY-QA).
    passed_ref.update(_carried_brief_fields(draft_ref))
    db.set_result_ref(task_id, passed_ref)
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)

def qa_review_brand_steward_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    _single_draft_qa_review(
        task_id,
        envelope,
        db,
        task_name="qa-review-brand-steward",
        function_id=FUNCTION_ID_02,
        prompt_dir="02-brand-steward-qa",
        agent_name="brand-steward-qa",
        review_kind="brand_steward",
    )

def qa_review_fact_check_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    """Uses functions/48-fact-check-verdict/prompt.md, bounded strictly to
    weekly-content-loop.yaml's own stated Thursday fact-check criterion.
    See module docstring for the one limitation left open.

    TD-35: the prompt file claims Pieter signed it off as settled QA
    policy on 2 Sep 2026, but that claim was written by an engineering
    session, not confirmed by Pieter through any channel this repository
    can verify -- see functions/48-fact-check-verdict/REVIEW-PACKET.md.
    This handler does not trust that prose. The actual gate is
    policies/fact-check-gate.yaml's `approved` flag, checked inside
    _single_draft_qa_review immediately before the model call (and again
    inside _run_qa_retry_loop, which re-runs this same review_kind on
    every regeneration attempt regardless of which sibling task triggered
    the retry) -- never the presence or absence of prompt.md's banner."""
    _single_draft_qa_review(
        task_id,
        envelope,
        db,
        task_name="qa-review-fact-check",
        function_id=FUNCTION_ID_48_FACT_CHECK,
        prompt_dir="48-fact-check-verdict",
        agent_name="fact-check-verdict",
        review_kind="fact_check",
    )

