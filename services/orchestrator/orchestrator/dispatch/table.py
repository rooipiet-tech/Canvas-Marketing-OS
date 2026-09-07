from __future__ import annotations

from typing import Any

from .handlers.approval import request_approval_handler
from .handlers.brief_rollups import (
    executive_brief_rollup_handler,
    morning_brief_rollup_handler,
    publish_brief_handler,
)
from .handlers.canva import draft_carousel_post_handler
from .handlers.client_permission import client_permission_request_handler
from .handlers.decision_quality import (
    decision_quality_evaluate_handler,
    decision_quality_level_review_monthly_handler,
)
from .handlers.dedupe import competitive_response_strategize_handler, dedupe_signal_cards_handler
from .handlers.draft_brief import draft_brief_handler
from .handlers.draft_content import draft_content_handler
from .handlers.drafting import draft_executive_ghostwrite_handler, draft_insight_to_story_handler
from .handlers.eval_generator import eval_generator_handler
from .handlers.expertise_corpus import expertise_corpus_mine_handler
from .handlers.foundation_drafter import foundation_drafter_bootstrap_handler
from .handlers.founder_position import propose_founder_position_handler
from .handlers.incident import incident_diagnose_handler
from .handlers.ingest import ingest_signals_handler
from .handlers.legal_triage import legal_triage_sweep_handler
from .handlers.misc_drafts import (
    draft_case_study_handler,
    draft_content_repurpose_handler,
    draft_newsletter_handler,
)
from .handlers.options import compose_options_handler, route_digest_handler
from .handlers.publish import publish_approved_assets_handler
from .handlers.qa_retry import qa_review_brand_steward_handler, qa_review_fact_check_handler
from .handlers.qa_review import qa_review_handler
from .handlers.reporting import report_month_end_handler
from .handlers.research_brief import (
    draft_client_advocacy_harvest_handler,
    draft_research_brief_handler,
)
from .handlers.sales_outcome import sales_outcome_infer_handler
from .handlers.scanner import SCANNER_HANDLERS
from .handlers.schedule_publish import publish_newsletter_handler, schedule_social_buffer_handler
from .handlers.scoring import score_signals_handler
from .handlers.source_lifecycle import (
    SOURCE_DISCOVERY_HANDLERS,
    source_retire_handler,
    source_yield_handler,
)
from .handlers.source_scout import probe_sources_handler, propose_sources_handler
from .handlers.standing_permission import standing_permission_learner_handler
from .handlers.visual_asset import visual_asset_compose_handler
from .handlers.voice_model import executive_voice_model_handler
from .handlers.web_reach import (
    WEB_REACH_REVIEW_HANDLERS,
    web_reach_allowlist_monthly_review_handler,
)
from .handlers.weekly_plan import plan_content_monday_handler

# ---------------------------------------------------------------------
# Dispatch table + legacy pass-through fallback (plan step 6; AC-01, AC-02)
# ---------------------------------------------------------------------

DISPATCH_TABLE: dict[str, Any] = {
    # The eleven S10 fan-out scanners, registered from one factory --
    # see SCANNER_TASKS and _make_scanner_handler above.
    **SCANNER_HANDLERS,
    # Fn 128's five daily discovery classes, registered from one factory
    # -- see SOURCE_DISCOVERY_TASKS and _make_source_discovery_handler
    # above (source-lifecycle-loop.yaml, Appendix D PR 5b).
    **SOURCE_DISCOVERY_HANDLERS,
    "source-yield-nightly": source_yield_handler,
    "source-retire-monthly": source_retire_handler,
    # Fn 129's five daily allowlist-rule reviews, registered from one
    # factory -- see WEB_REACH_REVIEW_TASKS and _make_web_reach_review_
    # handler above (source-lifecycle-loop.yaml, Appendix D PR 5c).
    **WEB_REACH_REVIEW_HANDLERS,
    "web-reach-allowlist-monthly-review": web_reach_allowlist_monthly_review_handler,
    # W1 + Appendix D PR 6/7 (Fn 126 Decision-Quality Evaluator, Fn 127
    # Eval Generator -- the measurement instrument).
    "decision-quality-evaluate": decision_quality_evaluate_handler,
    "decision-quality-level-review-monthly": decision_quality_level_review_monthly_handler,
    "eval-generator": eval_generator_handler,
    # Appendix D PR 8 (Fn 113 Expertise Corpus Miner, Fn 114 Executive
    # Voice Model).
    "expertise-corpus-mine": expertise_corpus_mine_handler,
    "executive-voice-model": executive_voice_model_handler,
    # Appendix D PR 9 (Fn 115 Position Proposer).
    "propose-founder-position": propose_founder_position_handler,
    # Appendix D PR 10 (Fn 118 Standing-Permission Learner).
    "standing-permission-learn": standing_permission_learner_handler,
    # Appendix D PR 11 (Fn 120 Sales Outcome Inferencer -- not wired, see
    # its own module docstring; Fn 124 Legal Triage; Fn 125 Incident
    # Autopilot -- no scheduled trigger, see its own module docstring).
    "sales-outcome-infer": sales_outcome_infer_handler,
    "legal-triage-sweep": legal_triage_sweep_handler,
    "incident-diagnose": incident_diagnose_handler,
    # Appendix D PR 12 (Fn 119 Client Permission Agent -- no scheduled
    # trigger, see its own module docstring; Fn 121 Visual Asset
    # Composer -- same; Fn 122 Foundation Drafter -- foundation-
    # bootstrap-loop.yaml).
    "client-permission-request": client_permission_request_handler,
    "visual-asset-compose": visual_asset_compose_handler,
    "foundation-drafter-bootstrap": foundation_drafter_bootstrap_handler,
    "ingest-signals": ingest_signals_handler,
    "propose-sources": propose_sources_handler,
    "probe-sources": probe_sources_handler,
    "publish-approved-assets": publish_approved_assets_handler,
    "report-month-end": report_month_end_handler,
    "dedupe-signal-cards": dedupe_signal_cards_handler,
    "competitive-response-strategize": competitive_response_strategize_handler,
    "morning-brief-rollup": morning_brief_rollup_handler,
    "executive-brief-rollup": executive_brief_rollup_handler,
    "publish-brief": publish_brief_handler,
    "score-signals": score_signals_handler,
    "draft-brief": draft_brief_handler,
    "qa-review": qa_review_handler,
    "draft-content": draft_content_handler,
    "request-approval": request_approval_handler,
    # S11 weekly-content-loop.yaml, 6 Aug 2026 -- see module docstring.
    "plan-content-monday": plan_content_monday_handler,
    "draft-research-brief": draft_research_brief_handler,
    "draft-client-advocacy-harvest": draft_client_advocacy_harvest_handler,
    "draft-insight-to-story": draft_insight_to_story_handler,
    "draft-executive-ghostwrite": draft_executive_ghostwrite_handler,
    "draft-carousel-post": draft_carousel_post_handler,
    "draft-newsletter": draft_newsletter_handler,
    "draft-case-study": draft_case_study_handler,
    "draft-content-repurpose": draft_content_repurpose_handler,
    "qa-review-brand-steward": qa_review_brand_steward_handler,
    "qa-review-fact-check": qa_review_fact_check_handler,
    "schedule-social-buffer": schedule_social_buffer_handler,
    "publish-newsletter": publish_newsletter_handler,
    # options-approval-loop.yaml (Appendix D PR 5).
    "compose-options": compose_options_handler,
    "route-digest": route_digest_handler,
}

