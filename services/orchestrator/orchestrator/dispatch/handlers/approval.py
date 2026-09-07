from __future__ import annotations

import base64
from typing import Any

from orchestrator.dispatch_errors import DispatchError
from orchestrator.models import TaskEnvelope, TaskStateEnum, TransitionReason
from orchestrator.telemetry_wiring import emit_task_span

from .. import clients
from ..core import is_proof_circuit, logger
from ..lineage import resolve_lineage_result
from ..qa_common import _reviewable_draft_text

# ---------------------------------------------------------------------
# request-approval (plan step 12; AC-01, AC-30) -- issues the gate-token
# request / surfaces the approval card, and NOTHING else. Completes the
# instant /gate-check responds; the human decision arrives asynchronously
# via Gatekeeper/approval-inbox, entirely outside this task.
# ---------------------------------------------------------------------

REAL_PUBLISH_FUNCTION_ID = "publish.social_post"
REAL_PUBLISH_ACTION_CLASS = "publish"
REAL_NEWSLETTER_FUNCTION_ID = "publish.blog_article"

# F-APPROVAL-CARD-BLIND (process 6). request_approval_handler passed
# content_hash and nothing else: preview_title stayed None for every real
# approval (it was set only on the proof-circuit path), and
# evidence_summary was never sent at all. The Gatekeeper's own fallbacks
# then produced, for EVERY content approval ever raised:
#
#   Approval required: publish.social_post (publish)
#   Preview:           publish.social_post (publish)
#   Evidence:          Autonomy policy requires human approval for
#                      publish.social_post / publish. Bound content hash:
#                      3f2a...
#
# Identical for this week's newsletter, last week's carousel and every
# story between them. The "evidence" says why the POLICY requires an
# approval; it says nothing about what is being approved. A human handed
# that card can click approve or reject, but cannot disagree with it --
# there is nothing there to disagree with.
#
# Everything needed was already on the lineage and simply not read. The
# QA gate forwards the draft's pillar, campaign and proof points
# (BRIEF_CARRIED_KEYS) and records its own verdict; the draft records
# which function wrote it. What follows turns that into a card a reviewer
# can actually act on.

APPROVAL_EXCERPT_CHARS = 400


def _approval_subject(draft_task_type: str | None) -> str:
    """A human name for the thing being approved, not a policy key."""
    return {
        "draft-insight-to-story": "Insight-to-story LinkedIn post",
        "draft-executive-ghostwrite": "Executive-voice LinkedIn post",
        "draft-carousel-post": "LinkedIn carousel",
        "draft-newsletter": "Owned-channel newsletter",
        "draft-case-study": "Case study",
        "draft-content-repurpose": "Repurposed social derivatives",
        "draft-brief": "Daily signal brief",
    }.get(draft_task_type or "", "Content asset")


def _approval_preview_title(ancestor_ref: dict[str, Any], draft_task_type: str | None) -> str:
    """One line that distinguishes THIS approval from every other.

    The subject, the pillar it was written to and the week's campaign tag
    -- the three things that differ between two pending cards. Falls back
    cleanly when a field is absent rather than printing "None"."""
    parts = [_approval_subject(draft_task_type)]
    pillar = ancestor_ref.get("pillar")
    if pillar:
        parts.append(str(pillar))
    campaign = ancestor_ref.get("campaign")
    if campaign:
        parts.append(str(campaign))
    return " — ".join(parts)


def _approval_evidence_summary(
    ancestor_ref: dict[str, Any],
    *,
    draft_task_type: str | None,
    draft_excerpt: str | None,
) -> str:
    """What a reviewer needs to disagree: the copy, where its claims came
    from, which reviews passed it, and how the week's subject was chosen.

    Deliberately assembled from what the lineage actually carries, with
    each absence stated rather than skipped -- "no proof points were
    supplied" is a reason to reject, so a card that silently omits the
    line is worse than one that says so."""
    lines: list[str] = []

    if draft_excerpt:
        excerpt = " ".join(draft_excerpt.split())
        if len(excerpt) > APPROVAL_EXCERPT_CHARS:
            excerpt = excerpt[:APPROVAL_EXCERPT_CHARS].rstrip() + "…"
        lines.append(f"Draft: {excerpt}")

    pillar = ancestor_ref.get("pillar")
    pillar_source = ancestor_ref.get("pillar_source")
    if pillar:
        chose = {
            "signals": "chosen from this week's scored signals",
            "rotation": "chosen by the calendar rotation, no signal evidence this week",
        }.get(str(pillar_source), "source not recorded")
        lines.append(f"Pillar: {pillar} ({chose}).")

    proof_points = ancestor_ref.get("proof_points") or []
    if proof_points:
        lines.append(f"Proof points ({len(proof_points)}), each as cited in the brief:")
        for point in proof_points:
            claim = str(point.get("claim", "")).strip()
            source = str(point.get("source", "")).strip()
            lines.append(f"  - {claim} [{source or 'no source recorded'}]")
    else:
        lines.append(
            "Proof points: none supplied. Every claim in this draft traces only to "
            "Canvas's standing approved facts, not to any evidence gathered this week."
        )

    verdicts = ancestor_ref.get("qa_verdicts")
    if verdicts:
        lines.append(f"Reviews passed: {', '.join(verdicts)}.")

    campaign = ancestor_ref.get("campaign")
    if campaign:
        lines.append(f"Attribution: every link carries utm_campaign={campaign}.")

    lines.append(
        f"Approving publishes this asset. Written by {_approval_subject(draft_task_type)}"
        f" ({draft_task_type or 'unknown task type'})."
    )
    return "\n".join(lines)


def _passed_review_kinds(task_id: str, db: Any) -> list[str]:
    """Which of this task's own review gates passed it.

    A Friday task depends on BOTH of its draft's Thursday gates but
    resolve_lineage_result stops at whichever one it reaches first, so the
    single ancestor_ref names only one review. Reading this task's own
    depends_on rows is what lets the card say "Brand Steward and
    fact-check both passed" truthfully instead of naming one and implying
    the other."""
    current = db.get_task(task_id) or {}
    rows = db.get_tasks(current.get("depends_on") or [])
    kinds = []
    for row in rows:
        ref = row.get("result_ref") or {}
        kind = ref.get("review_kind")
        if kind and ref.get("pass") and kind not in kinds:
            kinds.append(kind)
    return kinds


def _draft_excerpt_for_approval(vault: Any, ancestor_ref: dict[str, Any]) -> str | None:
    """The opening of the copy itself, or None when it cannot be read.

    Never fatal: a card missing its excerpt is worse than one without, but
    an approval that dead-letters because the excerpt could not be
    fetched is worse still -- the asset is already reviewed and the hash
    is already bound."""
    vault_asset_id = ancestor_ref.get("vault_asset_id")
    if not vault_asset_id:
        return None
    try:
        asset = vault.get_asset(vault_asset_id)
        text = base64.b64decode(asset["content_base64"]).decode("utf-8")
    except Exception:  # noqa: BLE001 - see docstring: never fatal
        logger.warning("approval_excerpt_unavailable", extra={"asset_id": str(vault_asset_id)})
        return None
    return _reviewable_draft_text(text)


def _approval_card_fields(
    task_id: str,
    db: Any,
    vault: Any,
    ancestor_ref: dict[str, Any],
    *,
    prefix: str | None = None,
) -> dict[str, str]:
    """The three human-facing fields every /gate-check approval carries.

    `prefix` preserves an existing call site's own marker (the loop-proof
    tag, the newsletter's "send NOT yet wired" caveat) in front of the
    generated title, so nothing that a reader already relies on is lost."""
    draft_task_type = ancestor_ref.get("draft_task_type")
    enriched = dict(ancestor_ref)
    enriched["qa_verdicts"] = _passed_review_kinds(task_id, db)
    title = _approval_preview_title(enriched, draft_task_type)
    return {
        "subject": _approval_subject(draft_task_type),
        "preview_title": f"{prefix} {title}" if prefix else title,
        "evidence_summary": _approval_evidence_summary(
            enriched,
            draft_task_type=draft_task_type,
            draft_excerpt=_draft_excerpt_for_approval(vault, ancestor_ref),
        ),
    }


def request_approval_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    """ROUND 34 (10 Aug 2026, confirmed live the night this loop's per-
    draft graph fix finally let a Friday task reach a real /gate-check
    call for the first time): contracts/vault-schema/schema.sql declares
    gate_decisions.agent_run_id NOT NULL FK -> agent_runs -- "the approving
    identity" must be a REAL
    row a handler actually inserted via vault.create_agent_run. This
    handler used to pass envelope.agent_run_id, a synthetic uuid5(event_id,
    source_task_id) the worker computes for tracing only (worker.py line
    ~139) and that NO handler ever writes to agent_runs -- so every real
    gate-check call from this handler was guaranteed to 500 with
    psycopg.errors.ForeignKeyViolation on gate_decisions_agent_run_id_fkey,
    live Postgres FK enforcement that dispatch tests never exercise since
    they mock the gatekeeper HTTP client. Uses the QA-gate ancestor's own
    agent_run_id instead (a real row qa_review_handler/_single_draft_qa_
    review already creates via vault.create_agent_run) -- same fix applied
    to schedule_social_buffer_handler and publish_newsletter_handler
    below."""
    lineage = resolve_lineage_result(task_id, db)
    if lineage is None:
        raise DispatchError("request-approval: no ancestor task carries a result_ref to approve")
    _ancestor_task, ancestor_ref = lineage
    content_hash = ancestor_ref.get("content_hash")
    if not content_hash:
        raise DispatchError("request-approval: ancestor result_ref carries no content_hash")
    approving_agent_run_id = ancestor_ref.get("agent_run_id")
    if not approving_agent_run_id:
        raise DispatchError("request-approval: ancestor result_ref carries no agent_run_id")

    proof_circuit = is_proof_circuit(envelope)
    # preview_reference: the programmatic/API-consumer tag (AC-15).
    # preview_title: the SAME tag, human-visible on the ONE field
    # console/app/templates/approvals.html actually renders (PV3-02) --
    # both are required, neither substitutes for the other.
    preview_reference = f"loop-proof://{task_id}" if proof_circuit else None

    with clients.build_vault_client() as vault:
        card = _approval_card_fields(
            task_id,
            db,
            vault,
            ancestor_ref,
            prefix="[LOOP-PROOF]" if proof_circuit else None,
        )

    with clients.build_gatekeeper_client() as gatekeeper:
        with emit_task_span(
            "request-approval",
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
                preview_reference=preview_reference,
                evidence_summary=card["evidence_summary"],
                subject=card["subject"],
            )

    db.set_result_ref(
        task_id,
        {
            "decision_id": decision.get("decision_id"),
            "outcome": decision.get("outcome"),
            "approval_id": decision.get("approval_id"),
            "approve_url": decision.get("approve_url"),
            "reject_url": decision.get("reject_url"),
            "content_hash": content_hash,
            "agent_run_id": str(approving_agent_run_id),
            "function_id": REAL_PUBLISH_FUNCTION_ID,
        },
    )
    # Completes as soon as /gate-check responds -- never waits/polls on
    # the human decision (AC-01's bounded scope for this task_type).
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)

