from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from orchestrator.clients.vault_client_ext import VaultClientExt
from orchestrator.dispatch_errors import DispatchError
from orchestrator.logging_config import log_event, sanitize_exception_text
from orchestrator.models import TaskEnvelope, TaskStateEnum, TransitionReason
from orchestrator.telemetry_wiring import emit_task_span

from .. import clients
from ..core import CONTENT_PILLARS, logger
from ..scan_shared import (
    RECENT_SIGNAL_SCAN_LIMIT,
    SCAN_BATCH_TYPES,
    _batch_items,
    _parse_iso_timestamp,
)
from . import scoring
from .scoring import ScoringPolicy, _score_signal

# =======================================================================
# S11 REAL HANDLERS (weekly-content-loop.yaml, 6 Aug 2026) -- see module
# docstring's "S11 REAL HANDLERS" section for full scope/caveats before
# reading further.
# =======================================================================

FUNCTION_ID_PLAN_COMPOSE = "plan.compose"

# ---------------------------------------------------------------------
# Connecting the daily loop to the weekly one (F-SCORES-UNREAD)
# ---------------------------------------------------------------------
#
# Scoring ranked signals and nothing read the ranking except the order of
# a bullet list in the morning brief. Meanwhile the weekly content loop --
# the one that produces everything Canvas actually publishes -- chose its
# pillar with CONTENT_PILLARS[week_number % 5], an ISO-week rotation that
# read no signal, no card and no score. The two halves of the system were
# not connected: the daily loop could report a market on fire and the
# weekly loop would still write about whatever the calendar said.
#
# Worse, function 41 (Research Brief Writer) received `{"pillar": ...}`
# alone. Its own schema requires `signal_summary`, described as "the raw
# signal or opportunity-card text this brief is built from -- a brief must
# never invent evidence the signal does not supply". It was being asked
# for a CITED brief with no sources, and the five Wednesday drafting
# functions all build on that brief, so every published asset inherited an
# unevidenced base.
#
# Both now read the same recent scored signals, through the same scoring
# rule score-signals itself uses (_score_signal), so there is one
# definition of "what matters" rather than two that can disagree.

RECENT_SIGNAL_LOOKBACK_DAYS = 7
BRIEF_SIGNAL_COUNT = 5


def _scored_from_cards(
    vault: VaultClientExt, cutoff: datetime
) -> list[dict[str, Any]]:
    """Recent opportunity_cards, highest-scored first, in the shape
    _recent_scored_signals' callers already expect.

    Returns [] rather than raising for every reason it might not be able
    to answer -- an unreachable Vault, no cards in the window, or cards
    predating the pillar column -- because its caller treats an empty
    result as "ask the signals instead", not as "the market was quiet".

    Cards with no pillar are skipped for the same reason the signal path
    skips fan-out scanner items: a card that cannot name one of Canvas's
    five pillars cannot vote on which pillar the week writes about, and
    bucketing it under a guess would be worse than not counting it.
    """
    try:
        cards = vault.list_opportunity_cards(limit=RECENT_SIGNAL_SCAN_LIMIT)
    except Exception as exc:  # noqa: BLE001 - see docstring
        log_event(
            logger,
            logging.WARNING,
            "recent_cards_unavailable",
            error=sanitize_exception_text(exc),
        )
        return []

    scored: list[dict[str, Any]] = []
    for card in cards:
        pillar = str(card.get("pillar") or "").strip()
        if pillar not in CONTENT_PILLARS:
            continue
        created = _parse_iso_timestamp(card.get("created_at"))
        if created is not None and created < cutoff:
            continue
        scored.append(
            {
                "headline": str(card.get("title") or ""),
                "so_what": str(card.get("so_what") or ""),
                "source_url": str(card.get("source_url") or ""),
                "pillar": pillar,
                "confidence": str(card.get("confidence") or ""),
                # The score the daily loop actually recorded, not a fresh
                # opinion of it: re-scoring here would let the weekly plan
                # silently disagree with the brief that was published.
                "score": float(card.get("score") or 0.0),
                "topic": "",
            }
        )
    scored.sort(key=lambda item: -item["score"])
    return scored


def _recent_scored_signals(
    vault: VaultClientExt, *, days: int = RECENT_SIGNAL_LOOKBACK_DAYS
) -> list[dict[str, Any]]:
    """Every signal recorded in the last `days`, scored with the SAME rule
    score-signals applies, highest first.

    Reads opportunity_cards -- score-signals' actual output -- and falls
    back to re-deriving from raw signal payloads when the cards cannot
    answer.

    This used to re-derive ALWAYS, because a card carried only a title and
    a score: the pillar this function selects on was not on it, and
    joining cards to signals on a headline string would have been a worse
    coupling than recomputing one arithmetic function. The post-v1
    additive columns (pillar, so_what, source_url, confidence) removed
    that reason, so the weekly loop now reads what the daily loop actually
    decided rather than recomputing its own opinion of it.

    THE FALLBACK IS NOT VESTIGIAL. Cards written before those columns
    existed carry no pillar, and a Vault upgraded mid-week holds a mix.
    Re-deriving in that case keeps planning on the evidence rather than
    reporting a quiet week that was not quiet -- it self-heals as the
    7-day window rolls past the upgrade.

    Never raises -- a Vault that is unreachable degrades planning to the
    calendar rotation it used before this existed, which is worse but not
    broken."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    scored: list[dict[str, Any]] = []
    # The same policy score-signals scores under, so the weekly loop's
    # idea of "what matters" cannot drift from the daily loop's. Read
    # once per call, not per signal.
    from_cards = _scored_from_cards(vault, cutoff)
    if from_cards:
        return from_cards

    # Only the fallback path needs the policy, and only to recompute
    # scores the cards would otherwise have carried. Loaded here, after
    # the cards have had their chance, so a policy this function cannot
    # read degrades planning rather than raising out of a function whose
    # contract is that it never does -- score-signals raises on the same
    # file every morning, which is where a bad policy should be loud.
    try:
        policy = scoring._load_scoring_policy()
    except DispatchError as exc:
        log_event(
            logger,
            logging.WARNING,
            "scoring_policy_unreadable",
            error=sanitize_exception_text(exc),
        )
        policy = ScoringPolicy()

    try:
        rows = vault.list_signals(limit=RECENT_SIGNAL_SCAN_LIMIT)
    except Exception as exc:  # noqa: BLE001 - see docstring
        log_event(
            logger,
            logging.WARNING,
            "recent_signals_unavailable",
            error=sanitize_exception_text(exc),
        )
        return []

    for row in rows:
        if row.get("signal_type") not in SCAN_BATCH_TYPES:
            continue
        received = _parse_iso_timestamp(row.get("received_at") or row.get("created_at"))
        if received is not None and received < cutoff:
            continue
        payload = row.get("payload") or {}
        for item in _batch_items(payload):
            pillar = str(item.get("pillar", "")).strip()
            if pillar not in CONTENT_PILLARS:
                # Fan-out scanner cards carry a taxonomy, not a pillar.
                # They are real signal but cannot vote on a pillar, so they
                # are skipped here rather than bucketed under a guess.
                continue
            scored.append(
                {
                    "headline": str(item.get("headline", "")),
                    "so_what": str(item.get("so_what", "")),
                    "source_url": str(item.get("source_url", "")),
                    "pillar": pillar,
                    "confidence": str(item.get("confidence", "")),
                    "score": _score_signal(item, policy),
                    "topic": str(payload.get("topic", "")),
                }
            )
    scored.sort(key=lambda item: -item["score"])
    return scored


def _top_pillar(scored: list[dict[str, Any]]) -> str | None:
    """The pillar the week's evidence points at: highest total score across
    its signals, not merely the most numerous, so three low-confidence
    mentions do not outweigh one well-evidenced move. Ties break by
    CONTENT_PILLARS order, so the same evidence always chooses the same
    pillar."""
    if not scored:
        return None
    totals: dict[str, float] = {}
    for item in scored:
        totals[item["pillar"]] = totals.get(item["pillar"], 0.0) + item["score"]
    return max(
        CONTENT_PILLARS,
        key=lambda pillar: (totals.get(pillar, 0.0), -CONTENT_PILLARS.index(pillar)),
    )


def _rotation_pillar() -> str:
    """The pre-existing behaviour, kept as the floor: reproducible, and
    never blocks planning when there is no evidence to plan from."""
    return CONTENT_PILLARS[datetime.now(timezone.utc).isocalendar()[1] % len(CONTENT_PILLARS)]


def plan_content_monday_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    """Monday planning is deterministic, NOT an LLM call -- there is
    nothing to draft yet, only a pillar to choose for the week. Rotates
    through CONTENT_PILLARS by ISO week number so the choice is
    reproducible and auditable (same week number -> same pillar) rather
    than random (Date.now()/random are unavailable in workflow-adjacent
    contexts elsewhere in this campaign for the same reason: reproducible
    beats clever). No Vault agent_run is created here -- there is no
    model call to meter, mirroring draft_brief_handler's own "no gateway
    call, no cost" span pattern.
    """
    week_number = datetime.now(timezone.utc).isocalendar()[1]

    # F-SCORES-UNREAD: the week's pillar now follows the evidence when
    # there is any, and falls back to the rotation when there is not --
    # so a quiet week or a failed scan never blocks planning, and the
    # previous behaviour remains the floor rather than becoming a new
    # failure mode. Which of the two decided is recorded, because "the
    # market chose this" and "the calendar chose this" are very different
    # claims to make about a week's content.
    with clients.build_vault_client() as vault:
        scored = _recent_scored_signals(vault)
    evidence_pillar = _top_pillar(scored)
    pillar = evidence_pillar or _rotation_pillar()
    pillar_source = "signals" if evidence_pillar else "rotation"
    log_event(
        logger,
        logging.INFO,
        "content_pillar_selected",
        pillar=pillar,
        pillar_source=pillar_source,
        scored_signal_count=len(scored),
        week_number=week_number,
    )

    with emit_task_span(
        "plan-content-monday",
        function_id=FUNCTION_ID_PLAN_COMPOSE,
        task_ref=task_id,
        model="none",
        cost=0.0,
        run_id=str(envelope.campaign_id),
    ):
        pass

    db.set_result_ref(
        task_id,
        {
            "pillar": pillar,
            "week_number": week_number,
            "pillar_source": pillar_source,
            "scored_signal_count": len(scored),
            # The evidence this week's plan rests on, carried forward so
            # function 41 builds its brief from the same signals that
            # chose the pillar rather than fetching its own view.
            "top_signals": [item for item in scored if item["pillar"] == pillar][
                :BRIEF_SIGNAL_COUNT
            ],
        },
    )
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)

# function 41's schema requires a `vertical` from a five-value enum, and
# nothing in the system can currently derive one honestly. Signals from
# the market-intelligence profile are sector-agnostic, and the six
# vertical profiles that WOULD name one carry no source urls yet, so they
# produce nothing to read. Rotating is a placeholder, not a judgement --
# it is recorded as such on the agent_run and the result_ref so nobody
# mistakes it for evidence, and it resolves itself the moment a vertical
# profile is sourced and its signals start carrying a real sector.
