from __future__ import annotations

import json
import logging
from typing import Any

from telemetry_lib import set_span_attribute

from orchestrator.clients.mcp_client import MCPClientError
from orchestrator.dispatch_errors import DispatchError
from orchestrator.logging_config import log_event, sanitize_exception_text
from orchestrator.models import TaskEnvelope, TaskStateEnum, TransitionReason
from orchestrator.telemetry_wiring import emit_task_span

from .. import clients, core
from ..completion import _complete_and_meter
from ..core import (
    FUNCTION_ID_39,
    FUNCTION_ID_43,
    _agent_name,
    _campaign_name,
    _now_iso,
    _parse_json_content,
    _read_prompt,
    _validate_function_input,
    logger,
)
from ..lineage import resolve_lineage_result


def _draft_social_post_handler(
    task_id: str,
    envelope: TaskEnvelope,
    db: Any,
    *,
    task_name: str,
    function_id: str,
    prompt_dir: str,
    agent_name: str,
    asset_type: str,
    render_draft_text: Any,
    build_payload: Any,
    max_tokens: int = 1536,
    on_draft_complete: Any = None,
) -> None:
    """Shared body for the 6 Wednesday drafting handlers that produce a
    single reviewable text asset from this week's research brief
    (insight-to-story, executive-ghostwrite, carousel, newsletter,
    case-study, content-repurpose). Each caller supplies its own
    `render_draft_text(output: dict) -> str` to flatten that function's
    own JSON output contract into the plain text qa_review_brand_steward_
    handler / qa_review_fact_check_handler will actually review -- the
    shape of that JSON differs per function (a carousel's slide array vs.
    a newsletter's subject+body), the review surface does not.

    Not a generalisation of qa_review_handler's ancestor-walk (that
    remains single-lineage, unchanged, still serving daily-signal-loop and
    the S8 proof circuit exactly as before) -- this is new code for a new
    loop, following the same call shape by convention, not by shared
    implementation.

    F-WEEKLY-LOOP-DRAFT-PUBLIC-SOURCE (7 Aug 2026, heartbeat round 20,
    Pieter's explicit ruling via AskUserQuestion: "Extend the exemption"):
    all 5 of weekly-content-loop's real drafting task types that route
    through this shared handler (draft-insight-to-story,
    draft-executive-ghostwrite, draft-carousel-post, draft-newsletter,
    draft-case-study -- draft-content-repurpose was cascade-dead-lettered
    as a downstream effect, not blocked directly) were dead-lettering on
    REDACTION_BLOCKED/full-name-like: this week's research brief
    legitimately names executives, clients, and case-study subjects, and
    every draft here is explicitly reviewed by qa_review_brand_steward_
    handler / qa_review_fact_check_handler (both already exempted, see
    _single_draft_qa_review) before it can ever reach approval. Setting
    content_class="public_source_content" on this call is correct for the
    same reason it was correct at F-INGEST-PUBLIC-SOURCE and
    qa_review_handler: the firewall's ruling is not being second-guessed,
    the class of content this handler sends is being accurately declared.
    Do not copy this to any other handler without its own equivalent,
    explicit Pieter sign-off recorded in that handler's own docstring.

    ``max_tokens`` (F-WEDNESDAY-DRAFT-TRUNCATION, 9 Aug 2026, heartbeat
    round 28): additive, default 1536 -- see _complete_and_meter's own
    note. Each of the 5 callers below now passes an explicit value sized
    to its own typical output length; see each caller's own comment for
    the reasoning.
    """
    lineage = resolve_lineage_result(task_id, db)
    if lineage is None:
        raise DispatchError(f"{task_name}: no research-brief ancestor carries a result_ref")
    _ancestor_task, ancestor_ref = lineage
    brief_id = ancestor_ref.get("brief_id")
    if not brief_id:
        raise DispatchError(f"{task_name}: ancestor result_ref carries no brief_id")

    # Built and validated BEFORE any vault work, so a function that cannot
    # honestly be called this week costs nothing and leaves no half-open
    # campaign or running agent_run behind it.
    try:
        payload = build_payload(ancestor_ref)
    except DraftNotAttempted as skip:
        _complete_undrafted(
            task_id, db, task_name=task_name, function_id=function_id, skip=skip
        )
        return
    _validate_function_input(function_id, payload)

    with clients.build_vault_client() as vault:
        campaign_id = vault.get_or_create_campaign(
            _campaign_name(envelope), function_id=function_id
        )

        agent_run = vault.create_agent_run_idempotent(
            task_id=task_id,
            db=db,
            agent_name=_agent_name(agent_name, envelope),
            campaign_id=campaign_id,
            function_id=function_id,
            status="running",
            input_payload={"brief_id": brief_id, **payload},
        )

        system_prompt = _read_prompt(prompt_dir)
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
                    max_tokens=max_tokens,
                )
            set_span_attribute(span, "cost", cost)

        output = _parse_json_content(response["content"])
        # Both halves of the contract now hold for every Wednesday draft:
        # the payload above was checked against schema.json's input, this
        # checks what came back against its output. render_draft_text
        # reads fields straight off `output` (a carousel's slide array, a
        # newsletter's subject and body), so an off-contract response
        # otherwise renders a quietly empty asset that reads as a real
        # draft all the way to Thursday's review.
        core._validate_function_output(function_id, output)
        draft_text = render_draft_text(output)
        asset = vault.create_asset(
            asset_type=asset_type,
            agent_run_id=agent_run["id"],
            campaign_id=campaign_id,
            function_id=function_id,
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
            "pillar": payload["pillar"],
            "campaign": payload["campaign"],
            # Carried one hop further than the draft needs it: Thursday's
            # fact-check resolves lineage to THIS task, and function 48's
            # List D is the {claim, source} evidence this draft was built
            # from. Without it here the check falls back to the standing
            # lists and calls the week's real, cited evidence fabricated.
            "proof_points": ancestor_ref.get("proof_points") or [],
        },
    )

    # A3 (2 Sep 2026): an optional per-function step that runs ONCE the
    # draft, its Vault asset and its result_ref all exist. Only the
    # carousel supplies one today (_generate_carousel_designs, which hands
    # function 45's Canva manifest to mcp-canva).
    #
    # Deliberately best-effort and deliberately AFTER set_result_ref. This
    # is enrichment, not the deliverable: the reviewable asset is already
    # written and Thursday's QA reads the slide copy, not the deck. A
    # Canva outage dead-lettering a perfectly good Wednesday draft would
    # be a strictly worse system than the one that never called Canva at
    # all -- which is exactly what this is replacing.
    if on_draft_complete is not None:
        try:
            on_draft_complete(task_id, output, db)
        except MCPClientError as exc:
            # The ordinary case while canva-refresh-token is unpopulated
            # and CMOS_CANVA_DRY_RUN is off: the server is there, the call
            # is not going to work. Logged distinctly so "Canva is
            # unreachable" never reads as "the drafting code has a bug".
            log_event(
                logger,
                logging.WARNING,
                "draft_post_step_unreachable",
                task_id=task_id,
                task_name=task_name,
                error=sanitize_exception_text(exc),
            )
        except Exception as exc:  # noqa: BLE001 - enrichment never fails a draft
            log_event(
                logger,
                logging.WARNING,
                "draft_post_step_failed",
                task_id=task_id,
                task_name=task_name,
                error=sanitize_exception_text(exc),
            )

    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)

# F-CAMPAIGN-TAG-INVENTED: every Wednesday drafting prompt requires the
# single call-to-action link to carry utm_source/utm_medium/utm_campaign
# ("39-insight-to-story-editor/prompt.md" line 133 and its four siblings),
# and every one of those functions' schema.json requires a `campaign`
# slug -- which the shared handler never sent. Six drafting models each
# invented their own utm_campaign for the same week's brief, so one
# week's assets carried six unrelated attribution tags and process 8
# ("measure") had nothing coherent to attribute to.
#
# Derived from the pillar rather than generated, so every asset built on
# one week's brief shares one tag, and the same pillar always produces the
# same tag. This is draft_content_repurpose_handler's own existing
# derivation, lifted to a shared helper so the six drafts and the
# repurposer cannot drift apart -- function 52 already did this correctly
# and alone.
def _campaign_slug(pillar: str) -> str:
    """Every CONTENT_PILLARS value maps to a slug matching the pattern all
    six schemas impose on `campaign` (^[a-z0-9]+(-[a-z0-9]+)*$)."""
    return pillar.lower().replace(" ", "-")


class DraftNotAttempted(Exception):
    """Raised by a Wednesday drafting payload builder when this week's
    evidence cannot honestly fill that function's required input fields.

    Not a failure. Two of the six drafting functions require facts nothing
    in the system holds -- function 43 an `executive_name`, function 47 a
    real client engagement's situation/approach/result -- and a third and
    fourth (45, 46) require at least one proof point to build from. The
    alternative to raising here is putting a placeholder into a required
    field, which for a ghostwritten executive voice or a case study means
    publishing a fabrication under someone's name. Pieter's standing
    direction (1 Sep 2026): no executive and no client engagement is to be
    named yet.

    The task completes rather than fails: nothing went wrong, there was
    simply nothing this function could truthfully be asked to write."""

    def __init__(self, status: str, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


def _complete_undrafted(
    task_id: str,
    db: Any,
    *,
    task_name: str,
    function_id: str,
    skip: DraftNotAttempted,
) -> None:
    """Terminal state for a drafting task that was deliberately not
    attempted: COMPLETED with a result_ref that says so and carries no
    vault_asset_id, which _single_draft_qa_review reads to distinguish
    'nothing was written on purpose' from 'a draft went missing'."""
    log_event(
        logger,
        logging.WARNING,
        "draft_not_attempted",
        task_name=task_name,
        function_id=function_id,
        status=skip.status,
        reason=skip.reason,
    )
    db.set_result_ref(
        task_id,
        {
            "status": skip.status,
            "function_id": function_id,
            "reason": skip.reason,
        },
    )
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)


def _proof_point_strings(ancestor_ref: dict[str, Any]) -> list[str]:
    """Flattens function 41's structured {claim, source} proof points into
    the plain strings functions 39/45/46 declare (`minLength: 10`).

    The source is kept, not dropped: "proof over platitude" is the whole
    point of carrying these, and a drafting function that cannot see where
    a claim came from cannot honour its own never-fabricate rule."""
    flattened = []
    for point in ancestor_ref.get("proof_points") or []:
        claim = str(point.get("claim", "")).strip()
        source = str(point.get("source", "")).strip()
        if not claim:
            continue
        flattened.append(f"{claim} (source: {source})" if source else claim)
    return flattened


def _require_pillar(task_name: str, ancestor_ref: dict[str, Any]) -> str:
    pillar = ancestor_ref.get("pillar")
    if not pillar:
        raise DispatchError(f"{task_name}: ancestor result_ref carries no pillar")
    return str(pillar)


NO_EVIDENCE_PROOF_POINT = (
    "No documented proof point was available for this week's brief - "
    "flag the evidence gap rather than making a claim."
)


def _build_insight_story_payload(ancestor_ref: dict[str, Any]) -> dict[str, Any]:
    """Function 39 takes ONE proof point. Where 45 and 46 must decline an
    evidence-free week, 39's own schema names the behaviour it wants
    instead -- `proof_point` is documented as "When no evidence has been
    documented yet, state that plainly here -- the editor must flag the
    gap rather than fabricate a proof point." So the gap statement below
    is the contract's own instruction, not a placeholder smuggled into a
    required field, and prompt.md line 120 has the matching rule on the
    model's side ("If the supplied `proof_point` plainly states that no
    evidence...")."""
    pillar = _require_pillar("draft-insight-to-story", ancestor_ref)
    proof_points = _proof_point_strings(ancestor_ref)
    payload: dict[str, Any] = {
        "pillar": pillar,
        "proof_point": proof_points[0] if proof_points else NO_EVIDENCE_PROOF_POINT,
        "campaign": _campaign_slug(pillar),
    }
    audience_note = ancestor_ref.get("audience_note")
    if audience_note:
        payload["audience_note"] = audience_note
    return payload


def _build_multi_proof_payload(
    task_name: str, ancestor_ref: dict[str, Any], *, max_points: int
) -> dict[str, Any]:
    """Functions 45 and 46 both require `proof_points` with `minItems: 1`
    and no gap-statement clause of 39's kind: a carousel is one proof point
    per slide, a newsletter is this week's proof points. With none, there
    is no honest call to make -- so the week produces no carousel and no
    newsletter rather than a fabricated one. The bound differs (6 slides
    vs 5 newsletter points), so it is passed in rather than assumed."""
    pillar = _require_pillar(task_name, ancestor_ref)
    proof_points = _proof_point_strings(ancestor_ref)
    if not proof_points:
        raise DraftNotAttempted(
            "no_evidence",
            f"{task_name}: this week's research brief carries no proof points, and "
            "this function's schema requires at least one -- declining to draft "
            "rather than fabricate evidence",
        )
    return {
        "pillar": pillar,
        "proof_points": proof_points[:max_points],
        "campaign": _campaign_slug(pillar),
    }


def _build_carousel_payload(ancestor_ref: dict[str, Any]) -> dict[str, Any]:
    return _build_multi_proof_payload("draft-carousel-post", ancestor_ref, max_points=6)


def _build_newsletter_payload(ancestor_ref: dict[str, Any]) -> dict[str, Any]:
    return _build_multi_proof_payload("draft-newsletter", ancestor_ref, max_points=5)


def _build_ghostwrite_payload(ancestor_ref: dict[str, Any]) -> dict[str, Any]:
    """Function 43 requires `executive_name` -- the person whose voice the
    piece is written in. Nothing in this repository supplies one: the
    string appears only inside 43's own package (schema, skill, evals,
    tool_check) and in no config, register or positioning document. A
    placeholder here is a real opinion attributed to a real person who
    never said it, which is the exact failure 43's own never-fabricate
    rule exists to prevent.

    Appendix D PR 9 added propose_founder_position_handler (Fn 115),
    which builds the content.founder_position card this function is
    meant to draft the CHOSEN option from -- see that section's own
    module docstring for why reading it here is a documented follow-up,
    not wired into this function today: this function's caller
    (_draft_social_post_handler) calls it before any Vault client opens,
    by design, and this gate is unrelated to Fn 115's own existence
    regardless -- executive_name remains the open, separate decision."""
    _require_pillar("draft-executive-ghostwrite", ancestor_ref)
    raise DraftNotAttempted(
        "no_executive_configured",
        "draft-executive-ghostwrite: function 43 requires executive_name and no "
        "executive has been configured -- a ghostwritten voice needs a real "
        "person, not a placeholder",
    )


def _build_case_study_payload(ancestor_ref: dict[str, Any]) -> dict[str, Any]:
    """Function 47 requires situation/approach/result -- a real client
    engagement. docs/permission-register.yaml is default-deny and nothing
    in it is CLEARED (Imperial and Rotork both UNCLEARED, no written
    permission held), so there is no engagement this may be written from,
    and a research brief is not one. The loop already excludes case
    studies from Friday's auto-schedule for the same reason; this closes
    the drafting side."""
    _require_pillar("draft-case-study", ancestor_ref)
    raise DraftNotAttempted(
        "no_cleared_engagement",
        "draft-case-study: function 47 requires a real engagement's "
        "situation/approach/result and no client engagement is cleared in "
        "docs/permission-register.yaml -- declining to invent one",
    )


def _render_simple_post(output: dict[str, Any]) -> str:
    return f"{output.get('post', '')}\n\n{output.get('cta_url', '')}".strip()

def draft_insight_to_story_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    _draft_social_post_handler(
        task_id,
        envelope,
        db,
        task_name="draft-insight-to-story",
        function_id=FUNCTION_ID_39,
        prompt_dir="39-insight-to-story-editor",
        agent_name="insight-to-story-editor",
        asset_type="linkedin_post",
        render_draft_text=_render_simple_post,
        build_payload=_build_insight_story_payload,
        max_tokens=2048,
    )

def draft_executive_ghostwrite_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    _draft_social_post_handler(
        task_id,
        envelope,
        db,
        task_name="draft-executive-ghostwrite",
        function_id=FUNCTION_ID_43,
        prompt_dir="43-executive-ghostwriter",
        agent_name="executive-ghostwriter",
        asset_type="linkedin_post",
        render_draft_text=_render_simple_post,
        build_payload=_build_ghostwrite_payload,
        max_tokens=2560,
    )

