from __future__ import annotations

import base64
import json
import logging
from typing import Any

from telemetry_lib import set_span_attribute

from orchestrator import brand_rules
from orchestrator.dispatch_errors import DispatchError
from orchestrator.logging_config import log_event
from orchestrator.models import TaskEnvelope, TaskStateEnum, TransitionReason
from orchestrator.telemetry_wiring import emit_task_span

from .. import clients
from ..completion import _complete_and_meter
from ..core import (
    FUNCTION_ID_02,
    PROOF_CIRCUIT_TAG,
    _agent_name,
    _campaign_name,
    _now_iso,
    _parse_json_content,
    _read_prompt,
    _validate_function_input,
    is_proof_circuit,
    logger,
)
from ..lineage import resolve_lineage_result
from ..qa_common import QA_VERDICT_UNSPECIFIED_FAILURE, _resolve_verdict, _teams_display_text
from .research_brief import _carried_brief_fields
from .scanner import _ancestor_is_quiet, _complete_quiet_scan_noop

# ---------------------------------------------------------------------
# qa-review (plan step 10; AC-01, AC-05, AC-06, AC-31(c))
#
# Invoked from TWO loop positions through this ONE handler: daily-signal-
# loop.yaml's brief-QA ('qa', predecessor 'draft' / draft-brief) and the
# S8 proof circuit's content-QA ('content-qa-review', predecessor
# 'draft-linkedin-post' / draft-content). WHAT to validate is resolved
# purely from depends_on lineage (never from a task_id naming
# convention); task.params.proof_circuit (carried via envelope.metadata,
# see worker.py's _task_metadata) is consulted ONLY to decide whether to
# tag this invocation's own Vault agent_run with AGENT_NAME_LOOP_PROOF.
# ---------------------------------------------------------------------

def qa_review_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    permission_check = clients.load_permission_check()

    lineage = resolve_lineage_result(task_id, db)
    if lineage is None:
        raise DispatchError("qa-review: no ancestor task carries a result_ref to validate")
    ancestor_task, ancestor_ref = lineage
    if _ancestor_is_quiet(ancestor_ref):
        _complete_quiet_scan_noop(
            task_id, db, stage="qa-review", reason="upstream reported a quiet scan"
        )
        return

    with clients.build_vault_client() as vault:
        campaign_id = vault.get_or_create_campaign(
            _campaign_name(envelope), function_id=FUNCTION_ID_02
        )

        client_references: list[str] = []
        content_class: str | None = None
        if ancestor_task["task_type"] == "draft-content":
            channel = "linkedin"
            content_class = "public_source_content"
            asset = vault.get_asset(ancestor_ref["vault_asset_id"])

            draft_text = base64.b64decode(asset["content_base64"]).decode("utf-8")
        else:
            channel = "internal-brief"
            content_class = "public_source_content"
            brief = vault.get_brief(ancestor_ref["brief_id"])
            draft_text = brief["body"] or ""

        agent_run = vault.create_agent_run_idempotent(
            task_id=task_id,
            db=db,
            agent_name=_agent_name("brand-steward-qa", envelope),
            campaign_id=campaign_id,
            function_id=FUNCTION_ID_02,
            status="running",
            input_payload={
                "channel": channel,
                "proof_circuit_tag": PROOF_CIRCUIT_TAG if is_proof_circuit(envelope) else None,
            },
        )

        system_prompt = _read_prompt("02-brand-steward-qa")
        qa_payload = {
            "draft_text": draft_text,
            "client_references": client_references,
            "channel": channel,
        }
        _validate_function_input(FUNCTION_ID_02, qa_payload)
        user_content = json.dumps(qa_payload)

        with emit_task_span(
            "qa-review",
            function_id=FUNCTION_ID_02,
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
                    content_class=content_class,
                )
            set_span_attribute(span, "cost", cost)

        verdict = _parse_json_content(response["content"])
        declared_pass, violations = _resolve_verdict(FUNCTION_ID_02, verdict)

        # Both halves of check 1: the names the caller declared it intends
        # to use, and the registered names the draft actually contains.
        # The weekly path had only the first and passed it a literal empty
        # list (F-CLEARANCE-CHECK-DEAD); this path passes real references,
        # but a name the model wrote in unasked was invisible to it too.
        uncleared = permission_check.find_uncleared_references(client_references)
        uncleared += permission_check.find_uncleared_in_text(draft_text)
        if uncleared and permission_check.VIOLATION_CODE not in violations:
            violations.append(permission_check.VIOLATION_CODE)
            log_event(
                logger,
                logging.WARNING,
                "qa_uncleared_client_reference_found",
                task_id=task_id,
                names=sorted({clearance.name for clearance in uncleared}),
            )

        # F-DAILY-QA-NO-BACKSTOP (live, deploy-pipeline run 4). The daily
        # loop's QA gate blocked its draft on sa-english-spelling and took
        # draft-content, qa-review, request-approval and publish-brief down
        # with it -- while running none of the deterministic backstop that
        # exists for exactly that code. brand_rules.py was written after a
        # live run blocked all six weekly drafts on these same two codes,
        # every one of which was re-checked by hand and found clean; it was
        # then wired into _single_draft_qa_review and the retry loop, and
        # this path was left without it. Same rule, same model, same
        # hallucination, no backstop.
        #
        # Safe to apply here for the reason the module's own docstring
        # gives: reconcile_violations only ever REMOVES sa-english-spelling
        # and unsupported-claim, never adds them, and touches none of the
        # other four checks -- so it cannot mask a real finding. A draft
        # that genuinely contains a US spelling still blocks.
        violations, dropped = brand_rules.reconcile_violations(violations, draft_text)
        if dropped:
            log_event(
                logger,
                logging.WARNING,
                "qa_review_false_positive_dropped",
                task_id=task_id,
                channel=channel,
                dropped_violations=dropped,
            )

        passed = not violations
        if passed and not declared_pass and not dropped:
            # See _single_draft_qa_review's own identical branch: a refusal
            # with no code is still a refusal, EXCEPT when reconciliation
            # is what emptied the list -- then an empty list is the correct
            # result of overriding a known false positive, not a
            # reasonless refusal.
            #
            # `not dropped` is load-bearing and was missing on the first
            # attempt at this fix. Without it the whole change is inert:
            # the model declares pass=false with a hallucinated
            # sa-english-spelling, reconciliation drops the code, and this
            # branch immediately re-blocks on
            # verdict-declared-failure-without-code -- the same dead
            # letter, a different label. A test caught it.
            violations = [QA_VERDICT_UNSPECIFIED_FAILURE]
            passed = False
            log_event(
                logger,
                logging.WARNING,
                "qa_verdict_failed_without_violation_code",
                task_id=task_id,
                # The model's own words are the only account of why it
                # refused -- but they are model output, so they must not go
                # to stdout (see _parse_json_content's own note: nothing
                # scans a model reply, in either direction). They are
                # persisted to this run's agent_run row instead, where the
                # Vault's retention policy and access controls govern them.
                # This field is how you find them.
                agent_run_id=agent_run["id"],
            )

        vault.update_agent_run(
            agent_run["id"],
            status="succeeded" if passed else "failed",
            # `notes` carries the model's own account of its verdict. It
            # lives here rather than in a log line because it is model
            # output that nothing has scanned: `output` is free-form
            # (contracts/vault-api.yaml AgentRunUpdate, additionalProperties
            # true), so this is additive, and the Vault already governs
            # retention and access for it.
            output_payload={
                "pass": passed,
                "violations": violations,
                "notes": verdict.get("notes"),
            },
            completed_at=_now_iso(),
        )

    if not passed:
        db.set_result_ref(
            task_id,
            {
                "pass": False,
                "violations": violations,
                "agent_run_id": agent_run["id"],
                "campaign_id": campaign_id,
            },
        )
        db.transition(task_id, TaskStateEnum.FAILED, TransitionReason.QA_BLOCKED)
        log_event(logger, logging.INFO, "qa_review_blocked", task_id=task_id, violations=violations)

        # Proposal C (qa-feedback-loop-proposal-2026-08-05.md): surface the
        # block as a "needs edit" card instead of letting it die silently.
        # Same AC-25 flag-gate as draft_brief_handler's teams_notify call --
        # no-ops until TEAMS_WEBHOOK_URL exists in Key Vault.
        from orchestrator import teams_notify

        teams_notify.notify_needs_edit(
            task_id=task_id,
            channel=channel,
            violations=violations,
            draft_excerpt=_teams_display_text(draft_text)[:280],
        )

        return  # never advance_dependents -- request-approval must never see this asset

    passed_ref: dict[str, Any] = {
        "pass": True,
        "vault_asset_id": ancestor_ref.get("vault_asset_id"),
        "brief_id": ancestor_ref.get("brief_id"),
        "content_hash": ancestor_ref.get("content_hash"),
        "draft_task_type": ancestor_task.get("task_type"),
        "review_kind": "brand_steward",
        "agent_run_id": agent_run["id"],
        "campaign_id": campaign_id,
    }
    passed_ref.update(_carried_brief_fields(ancestor_ref))
    db.set_result_ref(task_id, passed_ref)
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)

