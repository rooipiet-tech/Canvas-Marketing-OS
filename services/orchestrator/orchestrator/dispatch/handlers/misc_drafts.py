from __future__ import annotations

import base64
import json
from typing import Any

from telemetry_lib import set_span_attribute

from orchestrator.dispatch_errors import DispatchError
from orchestrator.models import TaskEnvelope, TaskStateEnum, TransitionReason
from orchestrator.telemetry_wiring import emit_task_span

from .. import clients
from ..completion import _complete_and_meter
from ..core import (
    FUNCTION_ID_46,
    FUNCTION_ID_47,
    FUNCTION_ID_52,
    _agent_name,
    _campaign_name,
    _now_iso,
    _parse_json_content,
    _read_prompt,
)
from .drafting import (
    DraftNotAttempted,
    _build_case_study_payload,
    _build_newsletter_payload,
    _campaign_slug,
    _complete_undrafted,
    _draft_social_post_handler,
)


def _render_newsletter(output: dict[str, Any]) -> str:
    return f"Subject: {output.get('subject', '')}\n\n{output.get('body', '')}"

def draft_newsletter_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    _draft_social_post_handler(
        task_id,
        envelope,
        db,
        task_name="draft-newsletter",
        function_id=FUNCTION_ID_46,
        prompt_dir="46-newsletter-writer",
        agent_name="newsletter-writer",
        asset_type="newsletter",
        render_draft_text=_render_newsletter,
        build_payload=_build_newsletter_payload,
        max_tokens=3584,
    )

def _render_case_study(output: dict[str, Any]) -> str:
    return json.dumps(output.get("case_study", output), indent=2)

def draft_case_study_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    """Case studies are intentionally excluded from Friday's auto-schedule
    (function 47's own prompt.md: human-initiated cadence only, never
    published without a CLEARED client) -- weekly-content-loop.yaml
    deliberately has no friday-schedule-social-buffer-* task depending on
    wednesday-draft-case-study's Thursday review tasks (round 34's
    per-draft graph; see docs/content-learnings.md)."""
    _draft_social_post_handler(
        task_id,
        envelope,
        db,
        task_name="draft-case-study",
        function_id=FUNCTION_ID_47,
        prompt_dir="47-case-study-writer",
        agent_name="case-study-writer",
        asset_type="case_study",
        render_draft_text=_render_case_study,
        build_payload=_build_case_study_payload,
        max_tokens=4096,
    )

def _render_repurpose(output: dict[str, Any]) -> str:
    lines = []
    for derivative in output.get("derivatives", []):
        lines.append(f"[{derivative.get('format', '?')}]")
        lines.append(derivative.get("post", ""))
        lines.append(derivative.get("cta_url", ""))
        lines.append("")
    return "\n".join(lines).strip()

# F-CONTENT-REPURPOSE-RACE (7 Aug 2026, heartbeat round 22 discovery, fixed
# round 23): function 52's actual input contract (schema.json,
# functions/52-content-repurposer/evals/*.json) is source_asset_summary +
# pillar + campaign + target_formats -- "one existing long-form asset,
# typically function 46's newsletter or function 47's case study"
# (prompt.md). draft-content-repurpose depends on BOTH wednesday-draft-
# newsletter and wednesday-draft-case-study directly (weekly-content-
# loop.yaml), NOT on tuesday-research-brief, so it was never going to have
# a brief_id-shaped result_ref to resolve at all.
#
# The original implementation below routed through _draft_social_post_
# handler exactly like the other 5 Wednesday drafters, which calls
# resolve_lineage_result -- a BFS that stops at the FIRST ancestor
# carrying ANY non-null result_ref. draft-newsletter's and draft-case-
# study's own result_ref (vault_asset_id/content_hash/pillar -- see
# _draft_social_post_handler's own db.set_result_ref call) never carries a
# brief_id, so the walk always stopped one hop too early and every run
# raised DispatchError("...ancestor result_ref carries no brief_id").
#
# Originally logged as a "race condition" (round 22's live trace showed it
# immediately following draft-newsletter's completion) -- rereading this
# code confirms it is NOT timing-dependent: F-DISPATCH-GATE already
# guarantees draft-content-repurpose cannot be dispatched until BOTH
# depends_on entries have reached COMPLETED (or this task would already
# have been cascade-dead-lettered via DependencyDeadLetteredError before
# reaching this handler at all -- see that class's own docstring). The
# failure was deterministic, 100% of the time, on every run that ever got
# this far; round 22 was simply the FIRST run where draft-newsletter and
# draft-case-study both completed for real (every earlier run had them
# dead-lettering upstream on REDACTION_BLOCKED, which is why this bug was
# masked until F-WEEKLY-LOOP-DRAFT-PUBLIC-SOURCE shipped). Round-22 naming
# kept as-is in the tracker/here for continuity across sessions.
_CONTENT_REPURPOSE_SOURCE_TASK_TYPES = ["draft-newsletter", "draft-case-study"]

# 2-3 shorter derivative formats per prompt.md's own framing ("2-3 shorter
# derivative social formats") -- all 3 of function 52's supported formats,
# maximizing what a single repurpose pass produces. schema.json bounds
# target_formats at 1-3 unique entries from exactly this set.
CONTENT_REPURPOSE_TARGET_FORMATS = ["linkedin_post", "x_post", "email_teaser"]

def _select_repurpose_source(task_id: str, db: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Reads draft-content-repurpose's own two direct depends_on rows
    (NOT a resolve_lineage_result walk -- see the F-CONTENT-REPURPOSE-RACE
    note above for why that mechanism is the wrong tool here) and returns
    whichever of draft-newsletter / draft-case-study carries a reviewable
    vault_asset_id, preferring the newsletter (the loop's primary owned-
    channel asset, and function 52's prompt.md lists it first) and falling
    back to the case study. Per this task's own original docstring and
    prompt.md ("typically function 46's newsletter or function 47's case
    study"), function 52 consumes ONE source asset, not a merge of both --
    "and/or" in the loop YAML's description, not "both, combined"."""
    current = db.get_task(task_id)
    dep_ids = current.get("depends_on") or []
    rows = {row["task_id"]: row for row in db.get_tasks(dep_ids)}
    by_type = {row.get("task_type"): row for row in rows.values()}
    for task_type in _CONTENT_REPURPOSE_SOURCE_TASK_TYPES:
        row = by_type.get(task_type)
        if row is not None and (row.get("result_ref") or {}).get("vault_asset_id"):
            return row, row["result_ref"]
    # Both sources skipped (a week with no proof points writes no
    # newsletter, and the case study is never drafted while no engagement
    # is cleared) -- there is genuinely nothing to repurpose, which is a
    # completed no-op rather than the dead letter a DispatchError raises.
    if all((row.get("result_ref") or {}).get("status") for row in rows.values()):
        raise DraftNotAttempted(
            "no_source_asset",
            "draft-content-repurpose: neither draft-newsletter nor draft-case-study "
            "produced an asset this week -- nothing to repurpose",
        )
    raise DispatchError(
        "draft-content-repurpose: neither draft-newsletter nor draft-case-study "
        "ancestor carries a reviewable vault_asset_id"
    )

def draft_content_repurpose_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    """Dedicated handler (round 23 fix, replacing the old _draft_social_
    post_handler delegation -- see F-CONTENT-REPURPOSE-RACE above): reads
    the actual drafted TEXT of its chosen source (newsletter or case
    study), not a research brief, matching function 52's real input
    contract (source_asset_summary + pillar + campaign + target_formats).

    content_class="public_source_content" is set on this call for the same
    reason F-WEEKLY-LOOP-DRAFT-PUBLIC-SOURCE's docstring requires its own
    explicit sign-off per new call site: the source text here is the
    newsletter/case-study draft's own already-written content, itself
    rendered from this week's research brief -- the SAME class of
    legitimately-public, name-bearing content Pieter's "Extend the
    exemption" ruling (7 Aug 2026, round 20) already covers, one hop
    downstream. draft-content-repurpose was never actually exercised under
    that ruling (it was cascade-dead-lettered upstream every time, per the
    note above) so this is a genuine new call site, not a copy-paste of an
    already-approved one -- flagged to Pieter for awareness alongside this
    fix rather than gated on a fresh AskUserQuestion, since without it this
    handler reproduces the identical REDACTION_BLOCKED failure the round-20
    ruling was written to prevent, on content of the same already-approved
    class, still gated behind the unchanged Thursday QA + Friday approval
    steps before anything publishes."""
    try:
        source_task, source_ref = _select_repurpose_source(task_id, db)
    except DraftNotAttempted as skip:
        _complete_undrafted(
            task_id,
            db,
            task_name="draft-content-repurpose",
            function_id=FUNCTION_ID_52,
            skip=skip,
        )
        return
    vault_asset_id = source_ref["vault_asset_id"]
    pillar = source_ref.get("pillar")
    if not pillar:
        raise DispatchError(
            "draft-content-repurpose: source ancestor result_ref carries no pillar"
        )
    campaign_slug = _campaign_slug(pillar)

    with clients.build_vault_client() as vault:
        campaign_id = vault.get_or_create_campaign(
            _campaign_name(envelope), function_id=FUNCTION_ID_52
        )
        source_asset = vault.get_asset(vault_asset_id)
        source_text = base64.b64decode(source_asset["content_base64"]).decode("utf-8")

        agent_run = vault.create_agent_run_idempotent(
            task_id=task_id,
            db=db,
            agent_name=_agent_name("content-repurposer", envelope),
            campaign_id=campaign_id,
            function_id=FUNCTION_ID_52,
            status="running",
            input_payload={
                "pillar": pillar,
                "campaign": campaign_slug,
                "source_task_type": source_task.get("task_type"),
            },
        )

        system_prompt = _read_prompt("52-content-repurposer")
        user_content = json.dumps(
            {
                "source_asset_summary": source_text,
                "pillar": pillar,
                "campaign": campaign_slug,
                "target_formats": CONTENT_REPURPOSE_TARGET_FORMATS,
            }
        )

        with emit_task_span(
            "draft-content-repurpose",
            function_id=FUNCTION_ID_52,
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
                    max_tokens=4096,
                )
            set_span_attribute(span, "cost", cost)

        output = _parse_json_content(response["content"])
        draft_text = _render_repurpose(output)
        asset = vault.create_asset(
            asset_type="content_derivatives",
            agent_run_id=agent_run["id"],
            campaign_id=campaign_id,
            function_id=FUNCTION_ID_52,
            content_bytes=draft_text.encode("utf-8"),
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
            "pillar": pillar,
        },
    )
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)

