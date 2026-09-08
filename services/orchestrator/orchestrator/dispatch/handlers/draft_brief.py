from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from orchestrator.dispatch_errors import DispatchError
from orchestrator.models import TaskEnvelope, TaskStateEnum, TransitionReason
from orchestrator.telemetry_wiring import emit_task_span

from .. import clients
from ..core import FUNCTION_ID_BRIEF_COMPOSE, _agent_name, _campaign_name, _now_iso
from ..lineage import resolve_lineage_result
from .scanner import _ancestor_is_quiet, _complete_quiet_scan_noop
from .scoring import SCORING_POLICY_PATH

# ---------------------------------------------------------------------
# draft-brief (plan step 9; AC-01, AC-25)
# ---------------------------------------------------------------------

def _order_by_ranking(
    signals: list[dict[str, Any]], ranking: list[dict[str, Any]] | None
) -> list[dict[str, Any]]:
    """Reorder a signal batch to match score-signals' ranking.

    Matched on source_url, the one field carried through both. A signal the
    ranking does not mention keeps its original relative position at the
    end rather than being dropped -- rendering fewer signals than the batch
    contains would be a silent edit, and this function renders, it does not
    curate. No ranking (a run where score-signals produced nothing) leaves
    the batch exactly as it was, which is the pre-scoring behaviour."""
    if not ranking:
        return signals
    order = {item.get("source_url"): index for index, item in enumerate(ranking)}
    unranked = len(order)
    return sorted(
        signals,
        key=lambda signal: order.get(signal.get("source_url"), unranked),
    )


def _lead_opportunity_card_id(ancestor_ref: dict[str, Any]) -> str | None:
    """The card a brief is recorded against: the batch's highest-scored one.

    F-BRIEF-CARD-UNLINKED: briefs.opportunity_card_id is in the frozen
    vault schema and was always NULL, even after score-signals started
    writing cards -- so the card->brief edge the data model draws existed
    only on paper, and "which opportunity produced this brief" was a
    question nobody could answer with a query.

    A brief covers the WHOLE batch, not one card, so no single id is the
    complete truth. The lead card is the honest choice available in a
    one-column FK: score-signals writes its cards in ranked order, so
    opportunity_card_ids[0] is the same signal _order_by_ranking puts at
    the top of the brief and first in the executive edition's top three.
    The FK therefore records what the brief leads with, which is the
    question people actually ask of it.

    Returns None when the lineage carries no cards -- a run whose
    resolved ancestor is ingest rather than score -- leaving the column
    NULL exactly as before rather than inventing a link.
    """
    card_ids = ancestor_ref.get("opportunity_card_ids") or []
    return str(card_ids[0]) if card_ids else None


def _split_held_back(
    signals: list[dict[str, Any]], ranking: list[dict[str, Any]] | None
) -> tuple[list[dict[str, Any]], int]:
    """Drop the signals score-signals' policy did not select, and report
    how many were dropped so the brief can say so.

    A ranking with no `selected` key at all -- every run before the
    scoring policy existed, and every policy with no cut configured --
    selects everything, so this is a no-op by default. A signal the
    ranking does not mention is kept, for the same reason
    _order_by_ranking keeps it: this renders, it does not curate.
    """
    if not ranking or not any("selected" in item for item in ranking):
        return signals, 0
    selected_urls = {
        item.get("source_url") for item in ranking if item.get("selected", True)
    }
    known_urls = {item.get("source_url") for item in ranking}
    kept = [
        signal
        for signal in signals
        if signal.get("source_url") in selected_urls
        or signal.get("source_url") not in known_urls
    ]
    return kept, len(signals) - len(kept)


def _render_brief(
    topic: str,
    signal_output: dict[str, Any],
    ranking: list[dict[str, Any]] | None = None,
) -> tuple[str, str]:
    """Deterministic rendering (NO LLM call, per plan step 9) of
    function 09's structured signal batch into a full brief + a
    condensed one-page executive edition. Returns (full_body,
    executive_body).

    `ranking` is score-signals' output when that task ran, so the brief --
    and especially the executive edition's top three -- leads with the
    best-evidenced signals rather than with whatever order the model
    happened to emit. Optional, so a brief rendered without a scoring
    ancestor still renders."""
    summary = signal_output.get("summary", "")
    signals = _order_by_ranking(list(signal_output.get("signals", [])), ranking)
    signals, held_back = _split_held_back(signals, ranking)

    full_lines = [f"# Morning Brief — {topic}", "", summary, "", "## Signals"]
    for item in signals:
        source_domain = urlparse(item.get("source_url", "")).hostname or "unknown-source"
        full_lines.append(
            f"- [{item.get('pillar', '?')}/{item.get('confidence', '?')}] "
            f"{item.get('headline', '')} — {item.get('so_what', '')} "
            f"(source: {source_domain})"
        )
    if held_back:
        # Never a silent cut. A reader who cannot see how much of the scan
        # was withheld cannot tell a quiet market from a narrow policy.
        full_lines.extend(
            [
                "",
                f"_{held_back} further signal(s) scored below the selection policy in "
                f"functions/{'/'.join(SCORING_POLICY_PATH)} and are not shown. Every "
                "scored signal is recorded as an opportunity card in the Vault._",
            ]
        )
    full_body = "\n".join(full_lines)

    exec_lines = [f"# Executive Edition — {topic}", "", summary, "", "## Top signals"]
    for item in signals[:3]:
        exec_lines.append(f"- {item.get('headline', '')}")
    executive_body = "\n".join(exec_lines)

    return full_body, executive_body

def draft_brief_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:

    lineage = resolve_lineage_result(task_id, db)
    if lineage is None:
        raise DispatchError("draft-brief: no ancestor task carries a result_ref to render from")
    _ancestor_task, ancestor_ref = lineage
    if _ancestor_is_quiet(ancestor_ref):
        _complete_quiet_scan_noop(
            task_id, db, stage="draft-brief", reason="upstream reported a quiet scan"
        )
        return
    signal_id = ancestor_ref.get("vault_signal_id")
    if not signal_id:
        raise DispatchError("draft-brief: ancestor result_ref carries no vault_signal_id")

    with clients.build_vault_client() as vault:
        campaign_id = vault.get_or_create_campaign(
            _campaign_name(envelope), function_id=FUNCTION_ID_BRIEF_COMPOSE
        )
        signal = vault.get_signal(signal_id)
        signal_output = signal.get("payload", {})
        topic = ancestor_ref.get("topic") or signal_output.get("topic", "morning brief")

        full_body, executive_body = _render_brief(
            topic, signal_output, ancestor_ref.get("ranking")
        )
        lead_card_id = _lead_opportunity_card_id(ancestor_ref)

        agent_run = vault.create_agent_run_idempotent(
            task_id=task_id,
            db=db,
            agent_name=_agent_name("brief-writer", envelope),
            campaign_id=campaign_id,
            function_id=FUNCTION_ID_BRIEF_COMPOSE,
            status="running",
            input_payload={"vault_signal_id": signal_id},
        )

        brief = vault.create_brief(
            title=f"Morning Brief — {topic}",
            body_text=full_body,
            campaign_id=campaign_id,
            function_id=FUNCTION_ID_BRIEF_COMPOSE,
            opportunity_card_id=lead_card_id,
        )
        executive_brief = vault.create_brief(
            title=f"Executive Edition — {topic}",
            body_text=executive_body,
            campaign_id=campaign_id,
            function_id=FUNCTION_ID_BRIEF_COMPOSE,
            # Both editions render the same batch and lead with the same
            # signal, so both point at the same card rather than the
            # executive cut silently claiming a different origin.
            opportunity_card_id=lead_card_id,
        )

        vault.update_agent_run(
            agent_run["id"],
            status="succeeded",
            output_payload={"brief_id": brief["id"], "executive_brief_id": executive_brief["id"]},
            completed_at=_now_iso(),
        )

        # F-BRIEF-ANNOUNCED-BEFORE-QA: this used to call
        # teams_notify.notify_brief_ready right here, the moment the brief
        # was created -- before the `qa` task had run at all. A brief that
        # then failed QA had already been sent to the team, and the block
        # that followed was invisible to whoever had read it.
        #
        # The announcement now lives in publish_brief_handler, which
        # depends on `qa` and therefore only runs once the gate has
        # passed: a failed qa never advances its dependents. Same
        # notification, moved to the point where it is true.

    with emit_task_span(
        "draft-brief",
        function_id=FUNCTION_ID_BRIEF_COMPOSE,
        task_ref=task_id,
        model="none",
        cost=0.0,
        run_id=str(envelope.campaign_id),
    ):
        pass  # deterministic rendering only -- no gateway call, no cost

    db.set_result_ref(
        task_id,
        {
            "brief_id": brief["id"],
            "executive_brief_id": executive_brief["id"],
            "agent_run_id": agent_run["id"],
            "campaign_id": campaign_id,
            "opportunity_card_id": lead_card_id,
        },
    )
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)

