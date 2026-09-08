"""orchestrator/dispatch/__init__.py — the real per-task-type dispatch
mechanism
(plan steps 6-13; AC-01, AC-02, AC-05, AC-06, AC-20, AC-24, AC-28, AC-30,
AC-31).

DISPATCH_TABLE maps the GOAL-mandated task_types (the original 5, plus the
13 weekly-content-loop.yaml (S11) task_types added 6 Aug 2026 -- see
"S11 REAL HANDLERS" below) to a real handler that produces a real downstream
artifact -- a costs-table row (via a real model-gateway call), a Vault
signal/brief/asset, or a gate-token request + approval-inbox card. Every
task_type NOT in this table (any genuinely unregistered one) falls through
to legacy_task_pass_through, which is BYTE-IDENTICAL to worker.py's own
pre-session unconditional stub (RUNNING -> COMPLETED -> advance_dependents)
-- nothing here regresses an already-shipped loop (AC-02).

request-approval's scope is bounded exactly as AC-01 requires: it calls
Gatekeeper's /gate-check once and completes as soon as that responds. It
NEVER polls or waits for the human decision -- that arrives asynchronously
via the Gatekeeper/approval-inbox surface, entirely outside this task.

draft-content / qa-review (when invoked with params.proof_circuit) /
request-approval together form the S8 PROOF CIRCUIT (AC-30): every
Vault agent_run they create is tagged agent_name=AGENT_NAME_LOOP_PROOF,
and request-approval's gate-check request carries the PROOF_CIRCUIT_TAG
in both preview_reference and preview_title so it is unmistakable on
every surface that renders it (console's approvals.html only renders
preview_title -- see request_approval_handler's docstring).

S11 REAL HANDLERS (6 Aug 2026, heartbeat round 19n follow-up, Pieter's
explicit instruction "please continue until its working"): weekly-content-
loop.yaml's 13 task_types previously all fell through to
legacy_task_pass_through -- meaning every "Fired: Succeeded" Monday run in
la-weekly-planning-trigger's history (including the two prior to this date)
produced NOTHING: no draft, no QA, no gate-check, no Teams card. Confirmed
live via ca-orchestrator's Log stream during a manual trigger fire: all 13
tasks dispatched within ~10 seconds of each other, ignoring depends_on
entirely, each just flipping RUNNING -> COMPLETED. Root-caused by reading
this file directly, not inferred.

Scope of this addition, and what is deliberately NOT included:
  - The 8 drafting handlers (plan-content-monday's dependents) are real:
    each calls its numbered function's real prompt.md via a real
    model-gateway completion, exactly mirroring draft_content_handler's
    existing pattern. No new prompts were needed for these 8 -- they
    already existed, just unwired.
  - qa-review-brand-steward and qa-review-fact-check were originally
    AGGREGATE gates (thursday-brand-steward-qa depended_on all 6
    Wednesday drafts at once, reviewed each independently, but resolved
    to ONE terminal state for the whole task -- so a single violation in
    any one of the 6 blocked friday-schedule-social-buffer and friday-
    publish-newsletter for every draft, including ones that individually
    passed both reviews cleanly). ROUND 34 (docs/content-learnings.md,
    the "batch-gating" finding, confirmed live the night of 10 Aug 2026):
    restructured to a true per-draft graph -- weekly-content-loop.yaml
    now has ONE Thursday review task per Wednesday draft per review_kind
    (12 total), each depends_on exactly one Wednesday draft, handled by
    _single_draft_qa_review (a close structural sibling of the existing
    single-ancestor qa_review_handler, not a generalisation of it -- see
    that function's own docstring for why they stay separate). A
    violation in one draft now dead-letters only that draft's own Friday
    task(s); every other draft's Thursday/Friday tasks are unaffected,
    since the dependency graph -- not any handler-level filtering -- is
    what provides the isolation.
  - qa-review-fact-check reuses the SAME per-draft QA mechanism as
    brand-steward, against a separate prompt (functions/48-fact-check-
    verdict/prompt.md). It is bounded strictly to weekly-content-loop.yaml's
    own stated Thursday fact-check criterion ("confirms every proof point
    traces to a cited source, no fabricated claim survives downstream") and
    invents no policy beyond that. One limitation, if the gate below is
    ever approved: a fabricated narrative carrying no number is outside
    what the check can catch -- see that prompt's known-limitations section.
    TD-35 (docs/architecture/09-technical-debt.md): the prompt file itself
    carries a note claiming Pieter signed it off as settled QA policy on
    2 Sep 2026, written by an engineering session with no way to confirm a
    review actually happened. That claim is not trusted here. Every
    qa-review-fact-check task instead reads policies/fact-check-gate.yaml's
    `approved` flag -- defaulting to false -- and fails closed (FAILED /
    QA_BLOCKED, logged, a Teams card posted, never silent) until a human
    flips it after an actual review. See qa_review_fact_check_handler and
    functions/48-fact-check-verdict/REVIEW-PACKET.md.
  - friday-schedule-social-buffer requests a REAL gate-check (function_id
    publish.social_post, mirroring request_approval_handler exactly) for
    each Wednesday draft eligible for Buffer scheduling. ROUND 34: this is
    now 4 separate per-draft tasks (one per eligible draft type -- see
    weekly-content-loop.yaml), each gated on that one draft's own 2
    Thursday review tasks, rather than one task iterating a batch capped
    at buffer_weekly_post_cap -- the weekly cap concept no longer applies
    at this granularity (case-study is still explicitly excluded per
    function 47's own prompt.md -- human-initiated cadence only). Each
    draft gets its OWN approval card, not one combined card, since
    gate_check's contract is one content_hash per call.
  - friday-publish-newsletter requests a real gate-check (function_id
    publish.blog_article) exactly like schedule-social-buffer. This task
    completing does NOT itself send an email -- per request-approval's own
    existing scope (AC-01), it only ever requests the approval and stops;
    the actual send happens later, out of band, driven by whatever
    approves the decision. As of this change, services/publisher has NO
    real email-sending integration at all (confirmed by reading its
    source directly) -- Pieter chose Microsoft Graph/M365 as the intended
    mechanism, but that requires an Entra ID app registration with
    Mail.Send permission and admin consent that only Pieter can create.
    Approving a newsletter card today will NOT send a real email until
    that registration exists and its credentials are wired into
    services/publisher/app/config.py -- this is called out explicitly so
    nobody mistakes an approved decision for a delivered newsletter.
"""

from __future__ import annotations

# Re-exported so `dispatch.AGENT_NAME_LOOP_PROOF`, `dispatch.TaskStateEnum`
# and `dispatch.TransitionReason` keep resolving for tests/handlers that
# read them off the module, same as every import below this point --
# these were plain module-level imports in the pre-split dispatch.py.
from governance_lib.constants import (
    AGENT_NAME_LOOP_PROOF as AGENT_NAME_LOOP_PROOF,  # noqa: PLC0414 - re-export
)

# C1 (pure move): the four exception types now live in their own
# module, at the bottom of the dependency graph so any extracted
# module can raise them without importing dispatch.py back. Re-exported
# here, and they are the SAME class objects -- `except DispatchError`
# below still catches one raised anywhere else.
from orchestrator.dispatch_errors import (
    DependencyDeadLetteredError as DependencyDeadLetteredError,  # noqa: PLC0414 - re-export
)
from orchestrator.dispatch_errors import (
    DispatchError as DispatchError,  # noqa: PLC0414 - re-export
)
from orchestrator.dispatch_errors import (
    TaskAlreadyTerminalError as TaskAlreadyTerminalError,  # noqa: PLC0414 - re-export
)
from orchestrator.dispatch_errors import (
    TaskNotReadyError as TaskNotReadyError,  # noqa: PLC0414 - re-export
)

# C1 (pure move): the evidence text-shaping helpers now live in their
# own module. Imported back here so `dispatch._shape_source_evidence`
# and `dispatch.INGEST_MAX_FEED_ITEMS` keep resolving for the tests and
# handlers that read them -- see dispatch_text.py's header for why only
# the patch-free helpers were moved.
from orchestrator.dispatch_text import (
    INGEST_MAX_FEED_ITEMS as INGEST_MAX_FEED_ITEMS,  # noqa: PLC0414 - re-export
)
from orchestrator.dispatch_text import (
    _clean_markup_text as _clean_markup_text,  # noqa: PLC0414 - re-export
)
from orchestrator.dispatch_text import (
    _feed_item_lines as _feed_item_lines,  # noqa: PLC0414 - re-export
)
from orchestrator.dispatch_text import (
    _shape_source_evidence as _shape_source_evidence,  # noqa: PLC0414 - re-export
)
from orchestrator.models import TaskStateEnum as TaskStateEnum  # noqa: PLC0414 - re-export
from orchestrator.models import TransitionReason as TransitionReason  # noqa: PLC0414 - re-export

from .clients import (
    _PERMISSION_CHECK_MODULE_NAME as _PERMISSION_CHECK_MODULE_NAME,  # noqa: PLC0414 - re-export
)
from .clients import build_gatekeeper_client as build_gatekeeper_client  # noqa: PLC0414 - re-export
from .clients import build_gateway_client as build_gateway_client  # noqa: PLC0414 - re-export
from .clients import build_mcp_canva_client as build_mcp_canva_client  # noqa: PLC0414 - re-export
from .clients import build_mcp_web_client as build_mcp_web_client  # noqa: PLC0414 - re-export
from .clients import build_publisher_client as build_publisher_client  # noqa: PLC0414 - re-export
from .clients import build_vault_client as build_vault_client  # noqa: PLC0414 - re-export
from .clients import load_permission_check as load_permission_check  # noqa: PLC0414 - re-export
from .completion import INGEST_MAX_TOKENS as INGEST_MAX_TOKENS  # noqa: PLC0414 - re-export
from .completion import _complete_and_meter as _complete_and_meter  # noqa: PLC0414 - re-export
from .completion import (
    _complete_ingest_with_redaction_fallback as _complete_ingest_with_redaction_fallback,  # noqa: PLC0414 - re-export
)
from .core import CONTENT_PILLARS as CONTENT_PILLARS  # noqa: PLC0414 - re-export
from .core import FUNCTION_ID_02 as FUNCTION_ID_02  # noqa: PLC0414 - re-export
from .core import FUNCTION_ID_09 as FUNCTION_ID_09  # noqa: PLC0414 - re-export
from .core import FUNCTION_ID_26 as FUNCTION_ID_26  # noqa: PLC0414 - re-export
from .core import FUNCTION_ID_39 as FUNCTION_ID_39  # noqa: PLC0414 - re-export
from .core import FUNCTION_ID_41 as FUNCTION_ID_41  # noqa: PLC0414 - re-export
from .core import FUNCTION_ID_42 as FUNCTION_ID_42  # noqa: PLC0414 - re-export
from .core import FUNCTION_ID_43 as FUNCTION_ID_43  # noqa: PLC0414 - re-export
from .core import FUNCTION_ID_45 as FUNCTION_ID_45  # noqa: PLC0414 - re-export
from .core import FUNCTION_ID_46 as FUNCTION_ID_46  # noqa: PLC0414 - re-export
from .core import FUNCTION_ID_47 as FUNCTION_ID_47  # noqa: PLC0414 - re-export
from .core import (
    FUNCTION_ID_48_FACT_CHECK as FUNCTION_ID_48_FACT_CHECK,  # noqa: PLC0414 - re-export
)
from .core import FUNCTION_ID_52 as FUNCTION_ID_52  # noqa: PLC0414 - re-export
from .core import FUNCTION_ID_116 as FUNCTION_ID_116  # noqa: PLC0414 - re-export
from .core import FUNCTION_ID_117 as FUNCTION_ID_117  # noqa: PLC0414 - re-export
from .core import (
    FUNCTION_ID_BRIEF_COMPOSE as FUNCTION_ID_BRIEF_COMPOSE,  # noqa: PLC0414 - re-export
)
from .core import MAX_LINEAGE_HOPS as MAX_LINEAGE_HOPS  # noqa: PLC0414 - re-export
from .core import PROOF_CIRCUIT_TAG as PROOF_CIRCUIT_TAG  # noqa: PLC0414 - re-export
from .core import _agent_name as _agent_name  # noqa: PLC0414 - re-export
from .core import _campaign_name as _campaign_name  # noqa: PLC0414 - re-export
from .core import (
    _load_function_input_schema as _load_function_input_schema,  # noqa: PLC0414 - re-export
)
from .core import (
    _load_function_output_schema as _load_function_output_schema,  # noqa: PLC0414 - re-export
)
from .core import _now_iso as _now_iso  # noqa: PLC0414 - re-export
from .core import _parse_json_content as _parse_json_content  # noqa: PLC0414 - re-export
from .core import _read_prompt as _read_prompt  # noqa: PLC0414 - re-export
from .core import _validate_function_input as _validate_function_input  # noqa: PLC0414 - re-export
from .core import (
    _validate_function_output as _validate_function_output,  # noqa: PLC0414 - re-export
)
from .core import is_proof_circuit as is_proof_circuit  # noqa: PLC0414 - re-export
from .core import logger as logger  # noqa: PLC0414 - re-export
from .gating import (
    _PERMANENTLY_BLOCKED_STATES as _PERMANENTLY_BLOCKED_STATES,  # noqa: PLC0414 - re-export
)
from .gating import _TERMINAL_STATES as _TERMINAL_STATES  # noqa: PLC0414 - re-export
from .gating import (
    _find_dead_lettered_dependency as _find_dead_lettered_dependency,  # noqa: PLC0414 - re-export
)
from .gating import dispatch_task as dispatch_task  # noqa: PLC0414 - re-export
from .gating import (
    legacy_task_pass_through as legacy_task_pass_through,  # noqa: PLC0414 - re-export
)
from .handlers.approval import (
    APPROVAL_EXCERPT_CHARS as APPROVAL_EXCERPT_CHARS,  # noqa: PLC0414 - re-export
)
from .handlers.approval import (
    REAL_NEWSLETTER_FUNCTION_ID as REAL_NEWSLETTER_FUNCTION_ID,  # noqa: PLC0414 - re-export
)
from .handlers.approval import (
    REAL_PUBLISH_ACTION_CLASS as REAL_PUBLISH_ACTION_CLASS,  # noqa: PLC0414 - re-export
)
from .handlers.approval import (
    REAL_PUBLISH_FUNCTION_ID as REAL_PUBLISH_FUNCTION_ID,  # noqa: PLC0414 - re-export
)
from .handlers.approval import (
    _approval_card_fields as _approval_card_fields,  # noqa: PLC0414 - re-export
)
from .handlers.approval import (
    _approval_evidence_summary as _approval_evidence_summary,  # noqa: PLC0414 - re-export
)
from .handlers.approval import (
    _approval_preview_title as _approval_preview_title,  # noqa: PLC0414 - re-export
)
from .handlers.approval import _approval_subject as _approval_subject  # noqa: PLC0414 - re-export
from .handlers.approval import (
    _draft_excerpt_for_approval as _draft_excerpt_for_approval,  # noqa: PLC0414 - re-export
)
from .handlers.approval import (
    _passed_review_kinds as _passed_review_kinds,  # noqa: PLC0414 - re-export
)
from .handlers.approval import (
    request_approval_handler as request_approval_handler,  # noqa: PLC0414 - re-export
)
from .handlers.brief_rollups import (
    _collect_rollup_inputs as _collect_rollup_inputs,  # noqa: PLC0414 - re-export
)
from .handlers.brief_rollups import _coverage_line as _coverage_line  # noqa: PLC0414 - re-export
from .handlers.brief_rollups import (
    _empty_cards_line as _empty_cards_line,  # noqa: PLC0414 - re-export
)
from .handlers.brief_rollups import (
    _render_intel_brief as _render_intel_brief,  # noqa: PLC0414 - re-export
)
from .handlers.brief_rollups import (
    executive_brief_rollup_handler as executive_brief_rollup_handler,  # noqa: PLC0414 - re-export
)
from .handlers.brief_rollups import (
    morning_brief_rollup_handler as morning_brief_rollup_handler,  # noqa: PLC0414 - re-export
)
from .handlers.brief_rollups import (
    publish_brief_handler as publish_brief_handler,  # noqa: PLC0414 - re-export
)
from .handlers.canva import (
    CANVA_MANIFEST_TEMPLATE_COLUMN as CANVA_MANIFEST_TEMPLATE_COLUMN,  # noqa: PLC0414 - re-export
)
from .handlers.canva import (
    CAROUSEL_BULK_CSV_MARKER as CAROUSEL_BULK_CSV_MARKER,  # noqa: PLC0414 - re-export
)
from .handlers.canva import (
    _generate_carousel_designs as _generate_carousel_designs,  # noqa: PLC0414 - re-export
)
from .handlers.canva import (
    _parse_canva_manifest as _parse_canva_manifest,  # noqa: PLC0414 - re-export
)
from .handlers.canva import _render_carousel as _render_carousel  # noqa: PLC0414 - re-export
from .handlers.canva import canva_dry_run as canva_dry_run  # noqa: PLC0414 - re-export
from .handlers.canva import (
    draft_carousel_post_handler as draft_carousel_post_handler,  # noqa: PLC0414 - re-export
)
from .handlers.client_permission import (
    _PERMISSION_REQUEST_LABEL_TO_OPTION_ID as _PERMISSION_REQUEST_LABEL_TO_OPTION_ID,  # noqa: PLC0414 - re-export
)
from .handlers.client_permission import (
    FUNCTION_ID_119 as FUNCTION_ID_119,  # noqa: PLC0414 - re-export
)
from .handlers.client_permission import (
    client_permission_request_handler as client_permission_request_handler,  # noqa: PLC0414 - re-export
)
from .handlers.decision_quality import (
    DECISION_HISTORY_WINDOW_DAYS as DECISION_HISTORY_WINDOW_DAYS,  # noqa: PLC0414 - re-export
)
from .handlers.decision_quality import (
    DECISION_QUALITY_SCORECARD_SIGNAL_TYPE as DECISION_QUALITY_SCORECARD_SIGNAL_TYPE,  # noqa: PLC0414 - re-export
)
from .handlers.decision_quality import (
    FUNCTION_ACTION_CLASS as FUNCTION_ACTION_CLASS,  # noqa: PLC0414 - re-export
)
from .handlers.decision_quality import (
    FUNCTION_ID_126 as FUNCTION_ID_126,  # noqa: PLC0414 - re-export
)
from .handlers.decision_quality import (
    LEVEL_REVIEW_PASS_MARKER_TYPE as LEVEL_REVIEW_PASS_MARKER_TYPE,  # noqa: PLC0414 - re-export
)
from .handlers.decision_quality import (
    LEVEL_REVIEW_PASS_MIN_DAYS as LEVEL_REVIEW_PASS_MIN_DAYS,  # noqa: PLC0414 - re-export
)
from .handlers.decision_quality import (
    REJECTION_CODE_TARGET_FUNCTION as REJECTION_CODE_TARGET_FUNCTION,  # noqa: PLC0414 - re-export
)
from .handlers.decision_quality import (
    _decision_quality_metrics_for as _decision_quality_metrics_for,  # noqa: PLC0414 - re-export
)
from .handlers.decision_quality import (
    decision_quality_evaluate_handler as decision_quality_evaluate_handler,  # noqa: PLC0414 - re-export
)
from .handlers.decision_quality import (
    decision_quality_level_review_monthly_handler as decision_quality_level_review_monthly_handler,  # noqa: PLC0414 - re-export
)
from .handlers.dedupe import DEDUPE_BATCH_TYPE as DEDUPE_BATCH_TYPE  # noqa: PLC0414 - re-export
from .handlers.dedupe import FUNCTION_ID_25 as FUNCTION_ID_25  # noqa: PLC0414 - re-export
from .handlers.dedupe import RESPONSE_PLAN_TYPE as RESPONSE_PLAN_TYPE  # noqa: PLC0414 - re-export
from .handlers.dedupe import STRATEGIST_CARD_CAP as STRATEGIST_CARD_CAP  # noqa: PLC0414 - re-export
from .handlers.dedupe import (
    STRATEGIST_CARD_KEYS as STRATEGIST_CARD_KEYS,  # noqa: PLC0414 - re-export
)
from .handlers.dedupe import _card_identity as _card_identity  # noqa: PLC0414 - re-export
from .handlers.dedupe import (
    _collect_scanner_batches as _collect_scanner_batches,  # noqa: PLC0414 - re-export
)
from .handlers.dedupe import _dedupe_cards as _dedupe_cards  # noqa: PLC0414 - re-export
from .handlers.dedupe import _rank_cards as _rank_cards  # noqa: PLC0414 - re-export
from .handlers.dedupe import _scanner_coverage as _scanner_coverage  # noqa: PLC0414 - re-export
from .handlers.dedupe import _strategist_cards as _strategist_cards  # noqa: PLC0414 - re-export
from .handlers.dedupe import (
    competitive_response_strategize_handler as competitive_response_strategize_handler,  # noqa: PLC0414 - re-export
)
from .handlers.dedupe import (
    dedupe_signal_cards_handler as dedupe_signal_cards_handler,  # noqa: PLC0414 - re-export
)
from .handlers.draft_brief import (
    _lead_opportunity_card_id as _lead_opportunity_card_id,  # noqa: PLC0414 - re-export
)
from .handlers.draft_brief import (
    _order_by_ranking as _order_by_ranking,  # noqa: PLC0414 - re-export
)
from .handlers.draft_brief import _render_brief as _render_brief  # noqa: PLC0414 - re-export
from .handlers.draft_brief import _split_held_back as _split_held_back  # noqa: PLC0414 - re-export
from .handlers.draft_brief import (
    draft_brief_handler as draft_brief_handler,  # noqa: PLC0414 - re-export
)
from .handlers.draft_content import (
    DRAFT_CONTENT_CAMPAIGN_UTM as DRAFT_CONTENT_CAMPAIGN_UTM,  # noqa: PLC0414 - re-export
)
from .handlers.draft_content import (
    DRAFT_CONTENT_PILLAR as DRAFT_CONTENT_PILLAR,  # noqa: PLC0414 - re-export
)
from .handlers.draft_content import (
    DRAFT_CONTENT_PROOF_POINT as DRAFT_CONTENT_PROOF_POINT,  # noqa: PLC0414 - re-export
)
from .handlers.draft_content import (
    draft_content_handler as draft_content_handler,  # noqa: PLC0414 - re-export
)
from .handlers.drafting import (
    NO_EVIDENCE_PROOF_POINT as NO_EVIDENCE_PROOF_POINT,  # noqa: PLC0414 - re-export
)
from .handlers.drafting import DraftNotAttempted as DraftNotAttempted  # noqa: PLC0414 - re-export
from .handlers.drafting import (
    _build_carousel_payload as _build_carousel_payload,  # noqa: PLC0414 - re-export
)
from .handlers.drafting import (
    _build_case_study_payload as _build_case_study_payload,  # noqa: PLC0414 - re-export
)
from .handlers.drafting import (
    _build_ghostwrite_payload as _build_ghostwrite_payload,  # noqa: PLC0414 - re-export
)
from .handlers.drafting import (
    _build_insight_story_payload as _build_insight_story_payload,  # noqa: PLC0414 - re-export
)
from .handlers.drafting import (
    _build_multi_proof_payload as _build_multi_proof_payload,  # noqa: PLC0414 - re-export
)
from .handlers.drafting import (
    _build_newsletter_payload as _build_newsletter_payload,  # noqa: PLC0414 - re-export
)
from .handlers.drafting import _campaign_slug as _campaign_slug  # noqa: PLC0414 - re-export
from .handlers.drafting import (
    _complete_undrafted as _complete_undrafted,  # noqa: PLC0414 - re-export
)
from .handlers.drafting import (
    _draft_social_post_handler as _draft_social_post_handler,  # noqa: PLC0414 - re-export
)
from .handlers.drafting import (
    _proof_point_strings as _proof_point_strings,  # noqa: PLC0414 - re-export
)
from .handlers.drafting import (
    _render_simple_post as _render_simple_post,  # noqa: PLC0414 - re-export
)
from .handlers.drafting import _require_pillar as _require_pillar  # noqa: PLC0414 - re-export
from .handlers.drafting import (
    draft_executive_ghostwrite_handler as draft_executive_ghostwrite_handler,  # noqa: PLC0414 - re-export
)
from .handlers.drafting import (
    draft_insight_to_story_handler as draft_insight_to_story_handler,  # noqa: PLC0414 - re-export
)
from .handlers.eval_generator import (
    EVAL_CASE_BATCH_SIGNAL_TYPE as EVAL_CASE_BATCH_SIGNAL_TYPE,  # noqa: PLC0414 - re-export
)
from .handlers.eval_generator import FUNCTION_ID_127 as FUNCTION_ID_127  # noqa: PLC0414 - re-export
from .handlers.eval_generator import (
    PRODUCTION_FAILURE_OUTCOMES as PRODUCTION_FAILURE_OUTCOMES,  # noqa: PLC0414 - re-export
)
from .handlers.eval_generator import (
    _production_failures_for as _production_failures_for,  # noqa: PLC0414 - re-export
)
from .handlers.eval_generator import (
    eval_generator_handler as eval_generator_handler,  # noqa: PLC0414 - re-export
)
from .handlers.expertise_corpus import (
    CORPUS_ZERO_DELTA_ALARM_DAYS as CORPUS_ZERO_DELTA_ALARM_DAYS,  # noqa: PLC0414 - re-export
)
from .handlers.expertise_corpus import (
    EXPERTISE_ATOM_BATCH_SIGNAL_TYPE as EXPERTISE_ATOM_BATCH_SIGNAL_TYPE,  # noqa: PLC0414 - re-export
)
from .handlers.expertise_corpus import (
    FUNCTION_ID_113 as FUNCTION_ID_113,  # noqa: PLC0414 - re-export
)
from .handlers.expertise_corpus import (
    _existing_corpus_atom_texts as _existing_corpus_atom_texts,  # noqa: PLC0414 - re-export
)
from .handlers.expertise_corpus import (
    _normalize_atom_text as _normalize_atom_text,  # noqa: PLC0414 - re-export
)
from .handlers.expertise_corpus import (
    _positioning_md_path as _positioning_md_path,  # noqa: PLC0414 - re-export
)
from .handlers.expertise_corpus import (
    expertise_corpus_mine_handler as expertise_corpus_mine_handler,  # noqa: PLC0414 - re-export
)
from .handlers.foundation_drafter import (
    FOUNDATION_ARTEFACT_KINDS as FOUNDATION_ARTEFACT_KINDS,  # noqa: PLC0414 - re-export
)
from .handlers.foundation_drafter import (
    FOUNDATION_ARTEFACT_PUBLISHED_SIGNAL_TYPE as FOUNDATION_ARTEFACT_PUBLISHED_SIGNAL_TYPE,  # noqa: PLC0414 - re-export
)
from .handlers.foundation_drafter import (
    FOUNDATION_REFIT_WINDOW_DAYS as FOUNDATION_REFIT_WINDOW_DAYS,  # noqa: PLC0414 - re-export
)
from .handlers.foundation_drafter import (
    FUNCTION_ID_122 as FUNCTION_ID_122,  # noqa: PLC0414 - re-export
)
from .handlers.foundation_drafter import (
    _foundation_artefacts_due as _foundation_artefacts_due,  # noqa: PLC0414 - re-export
)
from .handlers.foundation_drafter import (
    foundation_drafter_bootstrap_handler as foundation_drafter_bootstrap_handler,  # noqa: PLC0414 - re-export
)
from .handlers.founder_position import (
    FUNCTION_ID_115 as FUNCTION_ID_115,  # noqa: PLC0414 - re-export
)
from .handlers.founder_position import (
    propose_founder_position_handler as propose_founder_position_handler,  # noqa: PLC0414 - re-export
)
from .handlers.incident import FUNCTION_ID_125 as FUNCTION_ID_125  # noqa: PLC0414 - re-export
from .handlers.incident import (
    STANDING_PERMISSION_SUSPENDED_SIGNAL_TYPE as STANDING_PERMISSION_SUSPENDED_SIGNAL_TYPE,  # noqa: PLC0414 - re-export
)
from .handlers.incident import (
    incident_diagnose_handler as incident_diagnose_handler,  # noqa: PLC0414 - re-export
)
from .handlers.ingest import (
    INGEST_ORDINARY_SIGNAL_COUNT as INGEST_ORDINARY_SIGNAL_COUNT,  # noqa: PLC0414 - re-export
)
from .handlers.ingest import (
    ingest_signals_handler as ingest_signals_handler,  # noqa: PLC0414 - re-export
)
from .handlers.legal_triage import FUNCTION_ID_124 as FUNCTION_ID_124  # noqa: PLC0414 - re-export
from .handlers.legal_triage import (
    LEGAL_TRIAGE_VERDICT_SIGNAL_TYPE as LEGAL_TRIAGE_VERDICT_SIGNAL_TYPE,  # noqa: PLC0414 - re-export
)
from .handlers.legal_triage import (
    _already_triaged_card_ids as _already_triaged_card_ids,  # noqa: PLC0414 - re-export
)
from .handlers.legal_triage import (
    _pending_card_text as _pending_card_text,  # noqa: PLC0414 - re-export
)
from .handlers.legal_triage import (
    legal_triage_sweep_handler as legal_triage_sweep_handler,  # noqa: PLC0414 - re-export
)
from .handlers.misc_drafts import (
    _CONTENT_REPURPOSE_SOURCE_TASK_TYPES as _CONTENT_REPURPOSE_SOURCE_TASK_TYPES,  # noqa: PLC0414 - re-export
)
from .handlers.misc_drafts import (
    CONTENT_REPURPOSE_TARGET_FORMATS as CONTENT_REPURPOSE_TARGET_FORMATS,  # noqa: PLC0414 - re-export
)
from .handlers.misc_drafts import (
    _render_case_study as _render_case_study,  # noqa: PLC0414 - re-export
)
from .handlers.misc_drafts import (
    _render_newsletter as _render_newsletter,  # noqa: PLC0414 - re-export
)
from .handlers.misc_drafts import (
    _render_repurpose as _render_repurpose,  # noqa: PLC0414 - re-export
)
from .handlers.misc_drafts import (
    _select_repurpose_source as _select_repurpose_source,  # noqa: PLC0414 - re-export
)
from .handlers.misc_drafts import (
    draft_case_study_handler as draft_case_study_handler,  # noqa: PLC0414 - re-export
)
from .handlers.misc_drafts import (
    draft_content_repurpose_handler as draft_content_repurpose_handler,  # noqa: PLC0414 - re-export
)
from .handlers.misc_drafts import (
    draft_newsletter_handler as draft_newsletter_handler,  # noqa: PLC0414 - re-export
)
from .handlers.options import (
    _option_evidence_refs as _option_evidence_refs,  # noqa: PLC0414 - re-export
)
from .handlers.options import (
    _render_options_digest as _render_options_digest,  # noqa: PLC0414 - re-export
)
from .handlers.options import _run_option_qa as _run_option_qa  # noqa: PLC0414 - re-export
from .handlers.options import (
    compose_options_handler as compose_options_handler,  # noqa: PLC0414 - re-export
)
from .handlers.options import (
    route_digest_handler as route_digest_handler,  # noqa: PLC0414 - re-export
)
from .handlers.publish import (
    PUBLISH_CANDIDATE_TASK_TYPES as PUBLISH_CANDIDATE_TASK_TYPES,  # noqa: PLC0414 - re-export
)
from .handlers.publish import PUBLISH_CHANNEL as PUBLISH_CHANNEL  # noqa: PLC0414 - re-export
from .handlers.publish import (
    PUBLISH_STATUS_APPROVED as PUBLISH_STATUS_APPROVED,  # noqa: PLC0414 - re-export
)
from .handlers.publish import (
    PUBLISHABLE_FUNCTION_IDS as PUBLISHABLE_FUNCTION_IDS,  # noqa: PLC0414 - re-export
)
from .handlers.publish import _publish_one as _publish_one  # noqa: PLC0414 - re-export
from .handlers.publish import (
    publish_approved_assets_handler as publish_approved_assets_handler,  # noqa: PLC0414 - re-export
)
from .handlers.qa_retry import (
    _DRAFT_REGEN_PARAMS as _DRAFT_REGEN_PARAMS,  # noqa: PLC0414 - re-export
)
from .handlers.qa_retry import _QA_REVIEW_PARAMS as _QA_REVIEW_PARAMS  # noqa: PLC0414 - re-export
from .handlers.qa_retry import (
    FACT_CHECK_GATE_NOT_APPROVED as FACT_CHECK_GATE_NOT_APPROVED,  # noqa: PLC0414 - re-export
)
from .handlers.qa_retry import (
    MAX_QA_RETRY_ATTEMPTS as MAX_QA_RETRY_ATTEMPTS,  # noqa: PLC0414 - re-export
)
from .handlers.qa_retry import (
    _block_fact_check_gate_not_approved as _block_fact_check_gate_not_approved,  # noqa: PLC0414 - re-export
)
from .handlers.qa_retry import (
    _fact_check_gate_approved as _fact_check_gate_approved,  # noqa: PLC0414 - re-export
)
from .handlers.qa_retry import (
    _finalize_qa_failure as _finalize_qa_failure,  # noqa: PLC0414 - re-export
)
from .handlers.qa_retry import _looks_hollowed as _looks_hollowed  # noqa: PLC0414 - re-export
from .handlers.qa_retry import (
    _regenerate_draft_content as _regenerate_draft_content,  # noqa: PLC0414 - re-export
)
from .handlers.qa_retry import _run_qa_retry_loop as _run_qa_retry_loop  # noqa: PLC0414 - re-export
from .handlers.qa_retry import (
    _run_single_qa_check as _run_single_qa_check,  # noqa: PLC0414 - re-export
)
from .handlers.qa_retry import (
    _single_draft_qa_review as _single_draft_qa_review,  # noqa: PLC0414 - re-export
)
from .handlers.qa_retry import (
    qa_review_brand_steward_handler as qa_review_brand_steward_handler,  # noqa: PLC0414 - re-export
)
from .handlers.qa_retry import (
    qa_review_fact_check_handler as qa_review_fact_check_handler,  # noqa: PLC0414 - re-export
)
from .handlers.qa_review import qa_review_handler as qa_review_handler  # noqa: PLC0414 - re-export
from .handlers.reporting import (
    REPORT_FUNCTION_ID as REPORT_FUNCTION_ID,  # noqa: PLC0414 - re-export
)
from .handlers.reporting import _fmt_money as _fmt_money  # noqa: PLC0414 - re-export
from .handlers.reporting import _month_window as _month_window  # noqa: PLC0414 - re-export
from .handlers.reporting import (
    _render_month_end_report as _render_month_end_report,  # noqa: PLC0414 - re-export
)
from .handlers.reporting import _report_caveats as _report_caveats  # noqa: PLC0414 - re-export
from .handlers.reporting import (
    report_month_end_handler as report_month_end_handler,  # noqa: PLC0414 - re-export
)
from .handlers.research_brief import (
    BRIEF_CARRIED_KEYS as BRIEF_CARRIED_KEYS,  # noqa: PLC0414 - re-export
)
from .handlers.research_brief import BRIEF_VERTICALS as BRIEF_VERTICALS  # noqa: PLC0414 - re-export
from .handlers.research_brief import (
    _carried_brief_fields as _carried_brief_fields,  # noqa: PLC0414 - re-export
)
from .handlers.research_brief import _monday_plan as _monday_plan  # noqa: PLC0414 - re-export
from .handlers.research_brief import (
    _monday_plan_pillar as _monday_plan_pillar,  # noqa: PLC0414 - re-export
)
from .handlers.research_brief import (
    draft_client_advocacy_harvest_handler as draft_client_advocacy_harvest_handler,  # noqa: PLC0414 - re-export
)
from .handlers.research_brief import (
    draft_research_brief_handler as draft_research_brief_handler,  # noqa: PLC0414 - re-export
)
from .handlers.sales_outcome import FUNCTION_ID_120 as FUNCTION_ID_120  # noqa: PLC0414 - re-export
from .handlers.sales_outcome import (
    sales_outcome_infer_handler as sales_outcome_infer_handler,  # noqa: PLC0414 - re-export
)
from .handlers.scanner import SCANNER_HANDLERS as SCANNER_HANDLERS  # noqa: PLC0414 - re-export
from .handlers.scanner import SCANNER_TASKS as SCANNER_TASKS  # noqa: PLC0414 - re-export
from .handlers.scanner import _ancestor_is_quiet as _ancestor_is_quiet  # noqa: PLC0414 - re-export
from .handlers.scanner import (
    _complete_quiet_scan_noop as _complete_quiet_scan_noop,  # noqa: PLC0414 - re-export
)
from .handlers.scanner import (
    _complete_unconfigured_scan as _complete_unconfigured_scan,  # noqa: PLC0414 - re-export
)
from .handlers.scanner import (
    _make_scanner_handler as _make_scanner_handler,  # noqa: PLC0414 - re-export
)
from .handlers.schedule_publish import (
    _complete_nothing_to_publish as _complete_nothing_to_publish,  # noqa: PLC0414 - re-export
)
from .handlers.schedule_publish import (
    publish_newsletter_handler as publish_newsletter_handler,  # noqa: PLC0414 - re-export
)
from .handlers.schedule_publish import (
    schedule_social_buffer_handler as schedule_social_buffer_handler,  # noqa: PLC0414 - re-export
)
from .handlers.scoring import CONFIDENCE_SCORES as CONFIDENCE_SCORES  # noqa: PLC0414 - re-export
from .handlers.scoring import (
    FUNCTION_ID_SIGNAL_SCORE as FUNCTION_ID_SIGNAL_SCORE,  # noqa: PLC0414 - re-export
)
from .handlers.scoring import (
    MIN_SELECTED_SIGNALS as MIN_SELECTED_SIGNALS,  # noqa: PLC0414 - re-export
)
from .handlers.scoring import (
    SCORING_POLICY_PATH as SCORING_POLICY_PATH,  # noqa: PLC0414 - re-export
)
from .handlers.scoring import (
    UNKNOWN_CONFIDENCE_SCORE as UNKNOWN_CONFIDENCE_SCORE,  # noqa: PLC0414 - re-export
)
from .handlers.scoring import ScoringPolicy as ScoringPolicy  # noqa: PLC0414 - re-export
from .handlers.scoring import _apply_selection as _apply_selection  # noqa: PLC0414 - re-export
from .handlers.scoring import (
    _load_scoring_policy as _load_scoring_policy,  # noqa: PLC0414 - re-export
)
from .handlers.scoring import _rank_signals as _rank_signals  # noqa: PLC0414 - re-export
from .handlers.scoring import _score_signal as _score_signal  # noqa: PLC0414 - re-export
from .handlers.scoring import (
    score_signals_handler as score_signals_handler,  # noqa: PLC0414 - re-export
)
from .handlers.source_lifecycle import (
    _LAST_ITEM_DATE_RE as _LAST_ITEM_DATE_RE,  # noqa: PLC0414 - re-export
)
from .handlers.source_lifecycle import (
    BOOTSTRAP_CANDIDATES_PATH as BOOTSTRAP_CANDIDATES_PATH,  # noqa: PLC0414 - re-export
)
from .handlers.source_lifecycle import (
    FUNCTION_ID_128 as FUNCTION_ID_128,  # noqa: PLC0414 - re-export
)
from .handlers.source_lifecycle import (
    LIFECYCLE_SIGNAL_LOOKBACK as LIFECYCLE_SIGNAL_LOOKBACK,  # noqa: PLC0414 - re-export
)
from .handlers.source_lifecycle import (
    RETIRE_PASS_MARKER_TYPE as RETIRE_PASS_MARKER_TYPE,  # noqa: PLC0414 - re-export
)
from .handlers.source_lifecycle import (
    RETIRE_PASS_MIN_DAYS as RETIRE_PASS_MIN_DAYS,  # noqa: PLC0414 - re-export
)
from .handlers.source_lifecycle import (
    RETIRED_SOURCE_SIGNAL_TYPE as RETIRED_SOURCE_SIGNAL_TYPE,  # noqa: PLC0414 - re-export
)
from .handlers.source_lifecycle import (
    SCAN_PROFILE_SIGNAL_CLASS as SCAN_PROFILE_SIGNAL_CLASS,  # noqa: PLC0414 - re-export
)
from .handlers.source_lifecycle import (
    SIGNAL_CLASS_BOOTSTRAP_PROFILE_KEYS as SIGNAL_CLASS_BOOTSTRAP_PROFILE_KEYS,  # noqa: PLC0414 - re-export
)
from .handlers.source_lifecycle import (
    SOURCE_DISCOVERY_HANDLERS as SOURCE_DISCOVERY_HANDLERS,  # noqa: PLC0414 - re-export
)
from .handlers.source_lifecycle import (
    SOURCE_DISCOVERY_TASKS as SOURCE_DISCOVERY_TASKS,  # noqa: PLC0414 - re-export
)
from .handlers.source_lifecycle import (
    YIELD_FLOOR_CONSECUTIVE_FAILURES as YIELD_FLOOR_CONSECUTIVE_FAILURES,  # noqa: PLC0414 - re-export
)
from .handlers.source_lifecycle import (
    YIELD_SIGNAL_TYPE as YIELD_SIGNAL_TYPE,  # noqa: PLC0414 - re-export
)
from .handlers.source_lifecycle import (
    _bootstrap_candidate_pool as _bootstrap_candidate_pool,  # noqa: PLC0414 - re-export
)
from .handlers.source_lifecycle import (
    _client_domain_excluded as _client_domain_excluded,  # noqa: PLC0414 - re-export
)
from .handlers.source_lifecycle import (
    _expired_provisional_urls as _expired_provisional_urls,  # noqa: PLC0414 - re-export
)
from .handlers.source_lifecycle import (
    _forecast_yield_from_cadence as _forecast_yield_from_cadence,  # noqa: PLC0414 - re-export
)
from .handlers.source_lifecycle import (
    _freshness_days as _freshness_days,  # noqa: PLC0414 - re-export
)
from .handlers.source_lifecycle import (
    _live_source_urls as _live_source_urls,  # noqa: PLC0414 - re-export
)
from .handlers.source_lifecycle import (
    _load_bootstrap_document as _load_bootstrap_document,  # noqa: PLC0414 - re-export
)
from .handlers.source_lifecycle import (
    _make_source_discovery_handler as _make_source_discovery_handler,  # noqa: PLC0414 - re-export
)
from .handlers.source_lifecycle import (
    _retired_source_urls as _retired_source_urls,  # noqa: PLC0414 - re-export
)
from .handlers.source_lifecycle import (
    _source_lifecycle_options as _source_lifecycle_options,  # noqa: PLC0414 - re-export
)
from .handlers.source_lifecycle import (
    source_retire_handler as source_retire_handler,  # noqa: PLC0414 - re-export
)
from .handlers.source_lifecycle import (
    source_yield_handler as source_yield_handler,  # noqa: PLC0414 - re-export
)
from .handlers.source_scout import FUNCTION_ID_17 as FUNCTION_ID_17  # noqa: PLC0414 - re-export
from .handlers.source_scout import (
    FUNCTION_ID_SOURCE_PROMOTION as FUNCTION_ID_SOURCE_PROMOTION,  # noqa: PLC0414 - re-export
)
from .handlers.source_scout import (
    MIN_USEFUL_EXTRACTABLE_CHARS as MIN_USEFUL_EXTRACTABLE_CHARS,  # noqa: PLC0414 - re-export
)
from .handlers.source_scout import PROMOTE_SCORE as PROMOTE_SCORE  # noqa: PLC0414 - re-export
from .handlers.source_scout import (
    PROPOSAL_BATCH_TYPE as PROPOSAL_BATCH_TYPE,  # noqa: PLC0414 - re-export
)
from .handlers.source_scout import REJECT_SCORE as REJECT_SCORE  # noqa: PLC0414 - re-export
from .handlers.source_scout import (
    SOURCE_CANDIDATES_PATH as SOURCE_CANDIDATES_PATH,  # noqa: PLC0414 - re-export
)
from .handlers.source_scout import (
    SOURCE_PROMOTION_ACTION_CLASS as SOURCE_PROMOTION_ACTION_CLASS,  # noqa: PLC0414 - re-export
)
from .handlers.source_scout import (
    _known_candidate_urls as _known_candidate_urls,  # noqa: PLC0414 - re-export
)
from .handlers.source_scout import (
    _load_source_candidates as _load_source_candidates,  # noqa: PLC0414 - re-export
)
from .handlers.source_scout import (
    _profiles_needing_sources as _profiles_needing_sources,  # noqa: PLC0414 - re-export
)
from .handlers.source_scout import (
    _promotion_verdict as _promotion_verdict,  # noqa: PLC0414 - re-export
)
from .handlers.source_scout import (
    _render_promotion_evidence as _render_promotion_evidence,  # noqa: PLC0414 - re-export
)
from .handlers.source_scout import (
    _render_proposal_evidence as _render_proposal_evidence,  # noqa: PLC0414 - re-export
)
from .handlers.source_scout import _score_probe as _score_probe  # noqa: PLC0414 - re-export
from .handlers.source_scout import (
    probe_sources_handler as probe_sources_handler,  # noqa: PLC0414 - re-export
)
from .handlers.source_scout import (
    propose_sources_handler as propose_sources_handler,  # noqa: PLC0414 - re-export
)
from .handlers.standing_permission import (
    FUNCTION_ID_118 as FUNCTION_ID_118,  # noqa: PLC0414 - re-export
)
from .handlers.standing_permission import (
    STANDING_PERMISSION_MIN_DECISIONS as STANDING_PERMISSION_MIN_DECISIONS,  # noqa: PLC0414 - re-export
)
from .handlers.standing_permission import (
    STANDING_PERMISSION_MIN_HIT_RATE as STANDING_PERMISSION_MIN_HIT_RATE,  # noqa: PLC0414 - re-export
)
from .handlers.standing_permission import (
    STANDING_PERMISSION_PROPOSAL_SIGNAL_TYPE as STANDING_PERMISSION_PROPOSAL_SIGNAL_TYPE,  # noqa: PLC0414 - re-export
)
from .handlers.standing_permission import (
    STANDING_PERMISSION_SEEDED_MAX as STANDING_PERMISSION_SEEDED_MAX,  # noqa: PLC0414 - re-export
)
from .handlers.standing_permission import (
    STANDING_PERMISSION_WINDOW_DAYS as STANDING_PERMISSION_WINDOW_DAYS,  # noqa: PLC0414 - re-export
)
from .handlers.standing_permission import (
    _next_standing_permission_id as _next_standing_permission_id,  # noqa: PLC0414 - re-export
)
from .handlers.standing_permission import (
    standing_permission_learner_handler as standing_permission_learner_handler,  # noqa: PLC0414 - re-export
)
from .handlers.visual_asset import FUNCTION_ID_121 as FUNCTION_ID_121  # noqa: PLC0414 - re-export
from .handlers.visual_asset import (
    visual_asset_compose_handler as visual_asset_compose_handler,  # noqa: PLC0414 - re-export
)
from .handlers.voice_model import FUNCTION_ID_114 as FUNCTION_ID_114  # noqa: PLC0414 - re-export
from .handlers.voice_model import (
    VOICE_MODEL_LEADER as VOICE_MODEL_LEADER,  # noqa: PLC0414 - re-export
)
from .handlers.voice_model import (
    VOICE_PROFILE_DECISION_WINDOW_DAYS as VOICE_PROFILE_DECISION_WINDOW_DAYS,  # noqa: PLC0414 - re-export
)
from .handlers.voice_model import (
    VOICE_PROFILE_SIGNAL_TYPE as VOICE_PROFILE_SIGNAL_TYPE,  # noqa: PLC0414 - re-export
)
from .handlers.voice_model import (
    _latest_voice_profile as _latest_voice_profile,  # noqa: PLC0414 - re-export
)
from .handlers.voice_model import (
    executive_voice_model_handler as executive_voice_model_handler,  # noqa: PLC0414 - re-export
)
from .handlers.web_reach import (
    _COMPILED_INJECTION_PATTERNS as _COMPILED_INJECTION_PATTERNS,  # noqa: PLC0414 - re-export
)
from .handlers.web_reach import (
    _INJECTION_PATTERNS as _INJECTION_PATTERNS,  # noqa: PLC0414 - re-export
)
from .handlers.web_reach import (
    ALLOWLIST_REVIEW_PASS_MARKER_TYPE as ALLOWLIST_REVIEW_PASS_MARKER_TYPE,  # noqa: PLC0414 - re-export
)
from .handlers.web_reach import (
    ALLOWLIST_REVIEW_PASS_MIN_DAYS as ALLOWLIST_REVIEW_PASS_MIN_DAYS,  # noqa: PLC0414 - re-export
)
from .handlers.web_reach import (
    ALLOWLIST_WIDENED_SIGNAL_TYPE as ALLOWLIST_WIDENED_SIGNAL_TYPE,  # noqa: PLC0414 - re-export
)
from .handlers.web_reach import FUNCTION_ID_129 as FUNCTION_ID_129  # noqa: PLC0414 - re-export
from .handlers.web_reach import (
    WEB_REACH_REVIEW_HANDLERS as WEB_REACH_REVIEW_HANDLERS,  # noqa: PLC0414 - re-export
)
from .handlers.web_reach import (
    WEB_REACH_REVIEW_TASKS as WEB_REACH_REVIEW_TASKS,  # noqa: PLC0414 - re-export
)
from .handlers.web_reach import (
    _check_robots_directives as _check_robots_directives,  # noqa: PLC0414 - re-export
)
from .handlers.web_reach import (
    _domain_or_parent_in as _domain_or_parent_in,  # noqa: PLC0414 - re-export
)
from .handlers.web_reach import (
    _domain_registered_before_months as _domain_registered_before_months,  # noqa: PLC0414 - re-export
)
from .handlers.web_reach import (
    _evaluate_allowlist_rule as _evaluate_allowlist_rule,  # noqa: PLC0414 - re-export
)
from .handlers.web_reach import (
    _load_allowlist_deny as _load_allowlist_deny,  # noqa: PLC0414 - re-export
)
from .handlers.web_reach import (
    _load_allowlist_rule as _load_allowlist_rule,  # noqa: PLC0414 - re-export
)
from .handlers.web_reach import (
    _load_discovery_budget as _load_discovery_budget,  # noqa: PLC0414 - re-export
)
from .handlers.web_reach import (
    _load_standing_permissions_seed as _load_standing_permissions_seed,  # noqa: PLC0414 - re-export
)
from .handlers.web_reach import (
    _make_web_reach_review_handler as _make_web_reach_review_handler,  # noqa: PLC0414 - re-export
)
from .handlers.web_reach import (
    _render_allowlist_criteria_evidence as _render_allowlist_criteria_evidence,  # noqa: PLC0414 - re-export
)
from .handlers.web_reach import (
    _strip_instruction_shaped_content as _strip_instruction_shaped_content,  # noqa: PLC0414 - re-export
)
from .handlers.web_reach import (
    web_reach_allowlist_monthly_review_handler as web_reach_allowlist_monthly_review_handler,  # noqa: PLC0414 - re-export
)
from .handlers.weekly_plan import (
    BRIEF_SIGNAL_COUNT as BRIEF_SIGNAL_COUNT,  # noqa: PLC0414 - re-export
)
from .handlers.weekly_plan import (
    FUNCTION_ID_PLAN_COMPOSE as FUNCTION_ID_PLAN_COMPOSE,  # noqa: PLC0414 - re-export
)
from .handlers.weekly_plan import (
    RECENT_SIGNAL_LOOKBACK_DAYS as RECENT_SIGNAL_LOOKBACK_DAYS,  # noqa: PLC0414 - re-export
)
from .handlers.weekly_plan import (
    _recent_scored_signals as _recent_scored_signals,  # noqa: PLC0414 - re-export
)
from .handlers.weekly_plan import _rotation_pillar as _rotation_pillar  # noqa: PLC0414 - re-export
from .handlers.weekly_plan import (
    _scored_from_cards as _scored_from_cards,  # noqa: PLC0414 - re-export
)
from .handlers.weekly_plan import _top_pillar as _top_pillar  # noqa: PLC0414 - re-export
from .handlers.weekly_plan import (
    plan_content_monday_handler as plan_content_monday_handler,  # noqa: PLC0414 - re-export
)
from .lineage import resolve_lineage_result as resolve_lineage_result  # noqa: PLC0414 - re-export
from .qa_common import DRAFT_REVIEW_CHANNELS as DRAFT_REVIEW_CHANNELS  # noqa: PLC0414 - re-export
from .qa_common import (
    QA_VERDICT_UNSPECIFIED_FAILURE as QA_VERDICT_UNSPECIFIED_FAILURE,  # noqa: PLC0414 - re-export
)
from .qa_common import _resolve_verdict as _resolve_verdict  # noqa: PLC0414 - re-export
from .qa_common import _review_channel as _review_channel  # noqa: PLC0414 - re-export
from .qa_common import _reviewable_draft_text as _reviewable_draft_text  # noqa: PLC0414 - re-export
from .qa_common import _teams_display_text as _teams_display_text  # noqa: PLC0414 - re-export
from .scan_shared import CARD_BATCH_TYPE as CARD_BATCH_TYPE  # noqa: PLC0414 - re-export
from .scan_shared import (
    DEFAULT_INGEST_SOURCE_CHARS as DEFAULT_INGEST_SOURCE_CHARS,  # noqa: PLC0414 - re-export
)
from .scan_shared import (
    DEFAULT_MIN_INGEST_DOMAINS as DEFAULT_MIN_INGEST_DOMAINS,  # noqa: PLC0414 - re-export
)
from .scan_shared import (
    DEFAULT_MIN_INGEST_SOURCE_CHARS as DEFAULT_MIN_INGEST_SOURCE_CHARS,  # noqa: PLC0414 - re-export
)
from .scan_shared import (
    DEFAULT_MIN_INGEST_SOURCES as DEFAULT_MIN_INGEST_SOURCES,  # noqa: PLC0414 - re-export
)
from .scan_shared import (
    DEFAULT_SCAN_PROFILE_ID as DEFAULT_SCAN_PROFILE_ID,  # noqa: PLC0414 - re-export
)
from .scan_shared import PROBE_BATCH_TYPE as PROBE_BATCH_TYPE  # noqa: PLC0414 - re-export
from .scan_shared import QUIET_SCAN_STATUS as QUIET_SCAN_STATUS  # noqa: PLC0414 - re-export
from .scan_shared import (
    RECENT_SIGNAL_HEADLINE_CAP as RECENT_SIGNAL_HEADLINE_CAP,  # noqa: PLC0414 - re-export
)
from .scan_shared import (
    RECENT_SIGNAL_SCAN_LIMIT as RECENT_SIGNAL_SCAN_LIMIT,  # noqa: PLC0414 - re-export
)
from .scan_shared import SCAN_BATCH_TYPES as SCAN_BATCH_TYPES  # noqa: PLC0414 - re-export
from .scan_shared import SCAN_PROFILES_PATH as SCAN_PROFILES_PATH  # noqa: PLC0414 - re-export
from .scan_shared import SIGNAL_BATCH_TYPE as SIGNAL_BATCH_TYPE  # noqa: PLC0414 - re-export
from .scan_shared import _already_captured as _already_captured  # noqa: PLC0414 - re-export
from .scan_shared import _assert_ingest_floor as _assert_ingest_floor  # noqa: PLC0414 - re-export
from .scan_shared import (
    _assert_signal_domain_floor as _assert_signal_domain_floor,  # noqa: PLC0414 - re-export
)
from .scan_shared import _batch_items as _batch_items  # noqa: PLC0414 - re-export
from .scan_shared import (
    _build_ingest_user_content as _build_ingest_user_content,  # noqa: PLC0414 - re-export
)
from .scan_shared import _count_repeats as _count_repeats  # noqa: PLC0414 - re-export
from .scan_shared import _distinct_domains as _distinct_domains  # noqa: PLC0414 - re-export
from .scan_shared import (
    _envelope_scan_profile_id as _envelope_scan_profile_id,  # noqa: PLC0414 - re-export
)
from .scan_shared import _ingest_floors as _ingest_floors  # noqa: PLC0414 - re-export
from .scan_shared import (
    _ingest_min_source_chars as _ingest_min_source_chars,  # noqa: PLC0414 - re-export
)
from .scan_shared import _ingest_source_chars as _ingest_source_chars  # noqa: PLC0414 - re-export
from .scan_shared import _load_scan_profiles as _load_scan_profiles  # noqa: PLC0414 - re-export
from .scan_shared import _parse_iso_timestamp as _parse_iso_timestamp  # noqa: PLC0414 - re-export
from .scan_shared import _resolve_scan_profile as _resolve_scan_profile  # noqa: PLC0414 - re-export
from .scan_shared import _substantive_sources as _substantive_sources  # noqa: PLC0414 - re-export
from .table import DISPATCH_TABLE as DISPATCH_TABLE  # noqa: PLC0414 - re-export
