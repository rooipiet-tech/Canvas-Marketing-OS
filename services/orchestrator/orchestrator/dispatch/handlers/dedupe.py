from __future__ import annotations

import json
import logging
from typing import Any

from telemetry_lib import set_span_attribute

from orchestrator.dispatch_errors import DispatchError
from orchestrator.logging_config import log_event, sanitize_exception_text
from orchestrator.models import TaskEnvelope, TaskStateEnum, TransitionReason
from orchestrator.telemetry_wiring import emit_task_span

from .. import clients, core
from ..completion import _complete_and_meter
from ..core import (
    _agent_name,
    _campaign_name,
    _now_iso,
    _parse_json_content,
    _read_prompt,
    _validate_function_input,
    logger,
)
from ..lineage import resolve_lineage_result
from ..scan_shared import _batch_items
from .scoring import CONFIDENCE_SCORES, FUNCTION_ID_SIGNAL_SCORE

# =======================================================================
# THE SCANNERS' DEAD TAIL
# =======================================================================
#
# All eleven fan-out scanners fed `dedupe-signal-cards`, and every task
# from there down was unregistered -- falling through to
# legacy_task_pass_through, which sets no result_ref and completes the
# task "successfully" having done nothing:
#
#   11 scanners -> dedupe -> strategize -> morning-brief-rollup
#                                       -> executive-brief-rollup
#
# So the scanners ran every morning, cost a model call each, wrote a card
# batch into the Vault -- and nothing read any of it. The morning brief a
# person actually receives is built by draft-brief on the separate
# ingest -> score -> draft path, which never sees a single scanner card.
# Eleven scanners' worth of competitive intelligence went into the Vault
# and stopped.
#
# The rollups render deterministically. Ranking, deduplicating and
# formatting cards that other functions already wrote is arithmetic and
# string work; a model asked to "roll up" would only paraphrase, and
# every paraphrase is a chance to alter a claim that has already been
# through its own function's contract.

DEDUPE_BATCH_TYPE = "deduped_card_batch"
RESPONSE_PLAN_TYPE = "competitive_response_plan"
FUNCTION_ID_25 = "25-competitive-response-strategist"

# Function 25's input caps `cards` at 20 items.
STRATEGIST_CARD_CAP = 20

# Its input schema is additionalProperties:false and does NOT include
# `confidence`, which every scanner card carries -- so a card cannot be
# forwarded verbatim. These are exactly the keys function 25 accepts.
STRATEGIST_CARD_KEYS = (
    "headline",
    "so_what",
    "source_url",
    "card_type",
    "taxonomy",
    "evidence_grade",
)


def _card_identity(card: dict[str, Any]) -> tuple[str, str]:
    """The two things that make two cards the same story.

    A source_url match is the strong signal -- two scanners reaching the
    same article -- and a normalised headline catches the same story
    reported by two publications. Both are compared case- and
    whitespace-insensitively; neither alone is enough, which is why the
    caller checks each independently rather than combining them into one
    key."""
    url = str(card.get("source_url", "")).strip().lower().rstrip("/")
    headline = " ".join(str(card.get("headline", "")).lower().split())
    return url, headline


def _dedupe_cards(batches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One card per story, carrying how many scanners found it.

    `seen_by` is the point of doing this across eleven scanners rather
    than one: a story three separate profiles surfaced independently is a
    stronger signal than a story one did, and that fact exists nowhere
    until the batches are merged. Order is preserved -- first scanner to
    report a story keeps its position -- so the result is stable across
    runs given the same inputs.
    """
    merged: list[dict[str, Any]] = []
    urls: dict[str, int] = {}
    headlines: dict[str, int] = {}

    for batch in batches:
        for card in _batch_items(batch["payload"]):
            url, headline = _card_identity(card)
            index = urls.get(url) if url else None
            if index is None and headline:
                index = headlines.get(headline)
            if index is not None:
                existing = merged[index]
                existing["seen_by"] += 1
                if batch["profile_id"] not in existing["profiles"]:
                    existing["profiles"].append(batch["profile_id"])
                continue
            entry = dict(card)
            entry["seen_by"] = 1
            entry["profiles"] = [batch["profile_id"]]
            merged.append(entry)
            if url:
                urls[url] = len(merged) - 1
            if headline:
                headlines[headline] = len(merged) - 1
    return merged


def _rank_cards(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Most corroborated first, then best-evidenced, then most confident.

    Uses the same CONFIDENCE_SCORES score-signals uses, so "what matters"
    means one thing across the daily loop rather than two that can
    disagree."""
    grades = {"strong": 1.0, "moderate": 0.6, "light": 0.3}
    return sorted(
        cards,
        key=lambda card: (
            card.get("seen_by", 1),
            grades.get(str(card.get("evidence_grade", "")).lower(), 0.0),
            CONFIDENCE_SCORES.get(str(card.get("confidence", "")).lower(), 0.0),
        ),
        reverse=True,
    )


def _collect_scanner_batches(task_id: str, db: Any, vault: Any) -> list[dict[str, Any]]:
    """Every scanner card batch this task depends on.

    Reads this task's own depends_on rows rather than walking lineage:
    resolve_lineage_result stops at the FIRST ancestor carrying a
    result_ref, and this task has eleven of them -- taking one would
    silently discard ten scans. Same reasoning as
    _select_repurpose_source's own note.

    A scanner that completed as not_configured (no source urls on its
    profile) carries no vault_signal_id and is skipped, not failed: an
    unsourced profile is a known, recorded gap, not a reason to lose the
    ten scans that did run."""
    current = db.get_task(task_id) or {}
    batches: list[dict[str, Any]] = []
    for row in db.get_tasks(current.get("depends_on") or []):
        ref = row.get("result_ref") or {}
        signal_id = ref.get("vault_signal_id")
        if not signal_id:
            continue
        try:
            signal = vault.get_signal(signal_id)
        except Exception as exc:  # noqa: BLE001 - one unreadable batch must not sink the merge
            log_event(
                logger,
                logging.WARNING,
                "scanner_batch_unreadable",
                task_id=row.get("task_id"),
                signal_id=signal_id,
                error=sanitize_exception_text(exc),
            )
            continue
        batches.append(
            {
                "profile_id": ref.get("profile_id") or row.get("task_type"),
                "payload": signal.get("payload") or {},
            }
        )
    return batches


def _scanner_coverage(task_id: str, db: Any) -> dict[str, Any]:
    """How many of this task's scanners actually have sources, and which
    ones do not.

    _collect_scanner_batches skips a not_configured scanner with a bare
    `continue`, which is right for the merge -- an unsourced profile must
    not sink the scans that did run -- but it means the brief could not
    tell "every scanner found nothing" apart from "nine scanners have
    never been able to look". Those are opposite facts: one is a quiet
    market, the other is unfinished setup. This counts them separately so
    the brief can say which.

    Read off the same depends_on rows and the same `status` field
    _complete_unconfigured_scan writes, so there is one source of truth
    for what "dormant" means.
    """
    current = db.get_task(task_id) or {}
    configured: list[str] = []
    dormant: list[str] = []
    for row in db.get_tasks(current.get("depends_on") or []):
        ref = row.get("result_ref") or {}
        profile_id = ref.get("profile_id") or row.get("task_type") or "unknown"
        if ref.get("status") == "not_configured":
            dormant.append(str(profile_id))
        elif ref.get("vault_signal_id"):
            configured.append(str(profile_id))
    return {
        "configured_count": len(configured),
        "dormant_count": len(dormant),
        "scanner_total": len(configured) + len(dormant),
        "dormant_profiles": sorted(dormant),
    }


def dedupe_signal_cards_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    """Merges eleven scanners' card batches into one ranked, deduplicated
    set. Deterministic -- no model call."""
    coverage = _scanner_coverage(task_id, db)
    with clients.build_vault_client() as vault:
        batches = _collect_scanner_batches(task_id, db, vault)
        raw_count = sum(len(_batch_items(batch["payload"])) for batch in batches)
        cards = _rank_cards(_dedupe_cards(batches))

        campaign_id = vault.get_or_create_campaign(
            _campaign_name(envelope), function_id=FUNCTION_ID_SIGNAL_SCORE
        )
        signal = vault.create_signal(
            source="dedupe-signal-cards",
            signal_type=DEDUPE_BATCH_TYPE,
            payload={"cards": cards},
            campaign_id=campaign_id,
            function_id=FUNCTION_ID_SIGNAL_SCORE,
        )

    log_event(
        logger,
        logging.INFO,
        "signal_cards_deduped",
        scanners_read=len(batches),
        scanners_configured=coverage["configured_count"],
        scanners_dormant=coverage["dormant_count"],
        cards_in=raw_count,
        cards_out=len(cards),
        duplicates_removed=raw_count - len(cards),
    )
    db.set_result_ref(
        task_id,
        {
            "vault_signal_id": signal["id"],
            "campaign_id": campaign_id,
            "scanners_read": len(batches),
            "cards_in": raw_count,
            "cards_out": len(cards),
            "cards": cards,
            # Carried so the brief can distinguish a quiet market from
            # unfinished setup -- see _scanner_coverage.
            **coverage,
        },
    )
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)


def _strategist_cards(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduped cards reshaped to function 25's exact input contract.

    `confidence` and this handler's own `seen_by`/`profiles` are dropped:
    the schema is additionalProperties:false and lists neither, so a card
    forwarded verbatim is rejected. Cards missing a field the schema
    requires are dropped rather than patched -- inventing a card_type to
    satisfy a validator is how an unchecked claim gets into a plan."""
    shaped = []
    for card in cards[:STRATEGIST_CARD_CAP]:
        entry = {key: card[key] for key in STRATEGIST_CARD_KEYS if card.get(key)}
        if all(entry.get(key) for key in ("headline", "so_what", "source_url", "card_type")):
            shaped.append(entry)
    return shaped


def competitive_response_strategize_handler(
    task_id: str, envelope: TaskEnvelope, db: Any
) -> None:
    """Function 25 over the deduped cards: a severity-ranked response plan."""
    lineage = resolve_lineage_result(task_id, db)
    if lineage is None:
        raise DispatchError("competitive-response-strategize: no deduped-card ancestor")
    _ancestor_task, ancestor_ref = lineage
    cards = _strategist_cards(ancestor_ref.get("cards") or [])
    if not cards:
        # A morning where eleven scanners found nothing citable is a real
        # outcome, and a strategist asked to rank an empty list would be
        # asked to invent one. The rollup reads this and says so.
        log_event(logger, logging.WARNING, "competitive_response_no_cards", task_id=task_id)
        db.set_result_ref(
            task_id, {"status": "no_cards", "campaign_id": ancestor_ref.get("campaign_id")}
        )
        db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
        db.advance_dependents(task_id)
        return

    payload = {"cards": cards}
    _validate_function_input(FUNCTION_ID_25, payload)

    with clients.build_vault_client() as vault:
        campaign_id = vault.get_or_create_campaign(
            _campaign_name(envelope), function_id=FUNCTION_ID_25
        )
        agent_run = vault.create_agent_run_idempotent(
            task_id=task_id,
            db=db,
            agent_name=_agent_name("competitive-response-strategist", envelope),
            campaign_id=campaign_id,
            function_id=FUNCTION_ID_25,
            status="running",
            input_payload={"card_count": len(cards)},
        )
        system_prompt = _read_prompt(FUNCTION_ID_25)
        with emit_task_span(
            "competitive-response-strategize",
            function_id=FUNCTION_ID_25,
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
                    user_content=json.dumps(payload),
                    agent_run_id=agent_run["id"],
                    content_class="public_source_content",
                    max_tokens=3072,
                )
            set_span_attribute(span, "cost", cost)

        output = _parse_json_content(response["content"])
        core._validate_function_output(FUNCTION_ID_25, output)
        signal = vault.create_signal(
            source="competitive-response-strategize",
            signal_type=RESPONSE_PLAN_TYPE,
            payload=output,
            campaign_id=campaign_id,
            function_id=FUNCTION_ID_25,
        )
        vault.update_agent_run(
            agent_run["id"], status="succeeded", output_payload=output, completed_at=_now_iso()
        )

    db.set_result_ref(
        task_id,
        {
            "status": "planned",
            "vault_signal_id": signal["id"],
            "campaign_id": campaign_id,
            "agent_run_id": agent_run["id"],
            "summary": output.get("summary", ""),
            "response_plan": output.get("response_plan", []),
        },
    )
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)


