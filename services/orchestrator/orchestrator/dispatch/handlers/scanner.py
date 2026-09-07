from __future__ import annotations

import logging
from typing import Any

from telemetry_lib import set_span_attribute

from orchestrator.dispatch_errors import DispatchError
from orchestrator.dispatch_text import _shape_source_evidence
from orchestrator.logging_config import log_event, sanitize_exception_text
from orchestrator.models import TaskEnvelope, TaskStateEnum, TransitionReason
from orchestrator.telemetry_wiring import emit_task_span

from .. import clients, core, scan_shared
from ..completion import _complete_ingest_with_redaction_fallback
from ..core import (
    PROOF_CIRCUIT_TAG,
    _agent_name,
    _campaign_name,
    _now_iso,
    _parse_json_content,
    _read_prompt,
    is_proof_circuit,
    logger,
)
from ..scan_shared import (
    CARD_BATCH_TYPE,
    QUIET_SCAN_STATUS,
    SCAN_PROFILES_PATH,
    _assert_ingest_floor,
    _assert_signal_domain_floor,
    _batch_items,
    _count_repeats,
    _distinct_domains,
    _ingest_floors,
    _ingest_min_source_chars,
    _ingest_source_chars,
)

# ---------------------------------------------------------------------
# S10 intelligence fan-out — the eleven scanners (F1)
# ---------------------------------------------------------------------
#
# The architecture review's highest-value finding: eleven complete
# function packages -- prompt, schema, tools, skill, 5 evals each -- with
# a task in daily-signal-loop.yaml and NO DISPATCH_TABLE entry, so every
# one of them fell through to legacy_task_pass_through. The loop reported
# 23 completed tasks every morning while ~74% of its declared work
# produced nothing.
#
# ONE handler, eleven registrations. The eleven share an identical output
# contract -- {topic, horizon_days, [vertical], summary, cards[]} with
# card_type / taxonomy / evidence_grade / confidence per card -- so this
# is one function parameterised by (function_id, profile_id), not eleven
# near-copies of the kind C1 already flags this module for.
#
# UNSOURCED PROFILES COMPLETE, THEY DO NOT FAIL. All eleven profiles ship
# without urls today (see functions/_shared/scan-profiles.yaml's header:
# nobody has written down where to read each sector yet). Failing them
# would put eleven FAILED tasks on the board every morning and cascade
# into dedupe and both rollups -- making red the normal state, which is
# how a red loop stops meaning anything. Instead an unsourced scanner
# completes immediately with status="not_configured" on its result_ref
# and a warning naming the profile: no model call, no cost, and the
# emptiness is queryable rather than invisible, which is the actual
# difference from the no-op it replaces. Filling in that profile's urls
# is all it takes to make the scanner live -- no code change.
#
# Cards are persisted as a Vault signal batch, NOT as opportunity_cards
# rows. dedupe-signal-cards is still a no-op, and eleven scanners running
# the same three shared listening scopes will legitimately surface one
# event several times -- writing 11 batches straight to opportunity_cards
# would put that duplication in the table the morning brief reads. Card
# rows are dedupe's job when dedupe exists.

SCANNER_TASKS: dict[str, tuple[str, str, str]] = {
    # task_type: (function_id, default profile_id, agent_name)
    "competitor-discovery-scan": (
        "10-competitor-discovery-scanner",
        "competitor-discovery",
        "competitor-discovery-scanner",
    ),
    "competitor-change-monitor": (
        "11-competitor-change-monitor",
        "competitor-change",
        "competitor-change-monitor",
    ),
    "competitive-positioning-analysis": (
        "12-competitive-positioning-analyst",
        "competitive-positioning",
        "competitive-positioning-analyst",
    ),
    "competitor-content-performance-scout": (
        "13-competitor-content-performance-scout",
        "competitor-content-performance",
        "competitor-content-performance-scout",
    ),
    "fabric-ecosystem-scout": (
        "16-microsoft-fabric-ecosystem-scout",
        "fabric-ecosystem",
        "fabric-ecosystem-scout",
    ),
    "vertical-scan-logistics-fleet": (
        "18-01-vertical-intel-logistics-fleet",
        "vertical-logistics-fleet",
        "vertical-intel-logistics-fleet",
    ),
    "vertical-scan-mining-industrial": (
        "18-02-vertical-intel-mining-industrial",
        "vertical-mining-industrial",
        "vertical-intel-mining-industrial",
    ),
    "vertical-scan-manufacturing": (
        "18-03-vertical-intel-manufacturing",
        "vertical-manufacturing",
        "vertical-intel-manufacturing",
    ),
    "vertical-scan-construction": (
        "18-04-vertical-intel-construction",
        "vertical-construction",
        "vertical-intel-construction",
    ),
    "vertical-scan-fmcg-beverage": (
        "18-05-vertical-intel-fmcg-beverage",
        "vertical-fmcg-beverage",
        "vertical-intel-fmcg-beverage",
    ),
    "vertical-scan-financial-services": (
        "18-06-vertical-intel-financial-services",
        "vertical-financial-services",
        "vertical-intel-financial-services",
    ),
}


def _complete_quiet_scan_noop(task_id: str, db: Any, *, stage: str, reason: str) -> None:
    """Complete a brief-chain stage that has nothing to work on.

    F-INGEST-QUIET-ZERO. minItems went 3 -> 1 so a short honest batch
    would validate, but ZERO stayed invalid, and the contradiction rule 9
    describes survived at its edge: on a day where every retrieved source
    was already captured, the model's only schema-valid moves were to pad
    (breaking rule 9) or emit nothing and be rejected. Deploy run 9 hit
    exactly that -- 3 of 4 sources already captured, `[] should be
    non-empty`, three retries, dead-lettered, and ~20 descendants
    cascade-dead-lettered with it.

    The cascade is the real damage and it is mostly collateral: the
    ELEVEN fan-out scanners depend on `ingest` but do not consume its
    signals at all, so a quiet market-intelligence scan was taking down
    eleven unrelated scans plus the dedupe/rollup branch. Letting ingest
    COMPLETE on zero keeps every one of those running.

    Only the linear brief chain -- score -> draft -> qa -> publish -- has
    genuinely nothing to do, and it no-ops here rather than failing.
    Skipping is deliberate over publishing an empty brief: an approval
    request for nothing is worse than no brief, and this mirrors what
    _complete_unconfigured_scan already does for a sourceless scanner.

    WARNING, not INFO: "the market was quiet" is a claim worth being able
    to check against the evidence counts ingest_signals_quiet_scan
    carries, and a chain that silently produces no brief for a week must
    not look identical to one that is working."""
    log_event(
        logger,
        logging.WARNING,
        "brief_stage_skipped_quiet_scan",
        task_id=task_id,
        stage=stage,
        reason=reason,
    )
    db.set_result_ref(
        task_id,
        {"status": QUIET_SCAN_STATUS, "stage": stage, "reason": reason},
    )
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)


def _ancestor_is_quiet(ancestor_ref: dict[str, Any] | None) -> bool:
    """Did the stage upstream of this one report a quiet scan?

    Propagates down the chain: ingest marks itself quiet, score reads
    that and marks itself quiet, and so on to publish. One check, one
    status, no per-stage list of what to skip.
    """
    return bool(ancestor_ref) and ancestor_ref.get("status") == QUIET_SCAN_STATUS


def _complete_unconfigured_scan(
    task_id: str, db: Any, *, task_type: str, function_id: str, profile_id: str
) -> None:
    log_event(
        logger,
        logging.WARNING,
        "scan_profile_not_configured",
        task_type=task_type,
        function_id=function_id,
        profile_id=profile_id,
    )
    db.set_result_ref(
        task_id,
        {
            "status": "not_configured",
            "profile_id": profile_id,
            "function_id": function_id,
            "reason": (
                f"scan profile {profile_id!r} has no source urls in "
                f"functions/{'/'.join(SCAN_PROFILES_PATH)}"
            ),
        },
    )
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)


def _make_scanner_handler(task_type: str, function_id: str, profile_id: str, agent_name: str):
    """Build one of the eleven fan-out handlers. Structurally the same scan
    ingest_signals_handler runs -- fetch, floors, shaped evidence, redaction
    fallback, schema validation, cross-run memory -- against a different
    package's prompt and a different profile."""

    def handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
        resolved_profile_id = (
            str(envelope.metadata.get("profile_id")) if envelope.metadata else None
        ) or profile_id
        sources = scan_shared._resolve_scan_profile(resolved_profile_id, require_urls=False)
        if not sources.get("urls"):
            _complete_unconfigured_scan(
                task_id,
                db,
                task_type=task_type,
                function_id=function_id,
                profile_id=resolved_profile_id,
            )
            return

        configured_urls = list(sources["urls"])
        min_sources, min_domains = _ingest_floors(sources)
        source_chars = _ingest_source_chars(sources)
        min_source_chars = _ingest_min_source_chars(sources)

        with clients.build_mcp_web_client() as mcp:
            fetched: list[dict[str, str]] = []
            failed_urls: list[str] = []
            for url in configured_urls:
                try:
                    result = mcp.call_tool("fetch_url", {"url": url})
                except Exception as exc:  # noqa: BLE001 - one bad source must not sink the scan
                    failed_urls.append(url)
                    log_event(
                        logger,
                        logging.WARNING,
                        "fetch_url_failed",
                        url=url,
                        error=sanitize_exception_text(exc),
                    )
                    continue
                fetched.append(
                    {
                        "url": url,
                        "body": _shape_source_evidence(str(result.get("body", "")), source_chars),
                    }
                )

        if not fetched:
            raise DispatchError(
                f"{task_type}: every source configured for scan profile "
                f"{resolved_profile_id!r} failed to fetch"
            )
        _assert_ingest_floor("retrieval", fetched, min_sources, min_domains, min_source_chars)

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
                input_payload={
                    "topic": sources["topic"],
                    "horizon_days": sources["horizon_days"],
                    "source_urls": [item["url"] for item in fetched],
                    "source_urls_configured": configured_urls,
                    "scan_profile_id": resolved_profile_id,
                    "proof_circuit_tag": PROOF_CIRCUIT_TAG if is_proof_circuit(envelope) else None,
                },
            )

            system_prompt = _read_prompt(function_id)
            captured = scan_shared._already_captured(vault, sources)

            with emit_task_span(
                task_type,
                function_id=function_id,
                task_ref=task_id,
                model="claude-haiku",
                run_id=str(envelope.campaign_id),
            ) as span:
                with clients.build_gateway_client() as gateway:
                    response, cost, used_sources, skipped_sources = (
                        _complete_ingest_with_redaction_fallback(
                            gateway,
                            vault,
                            sources=sources,
                            fetched=fetched,
                            system_prompt=system_prompt,
                            agent_run_id=agent_run["id"],
                            captured=captured,
                        )
                    )
                set_span_attribute(span, "cost", cost)

            used_urls = [item["url"] for item in used_sources]
            _assert_ingest_floor(
                "the redaction fallback", used_sources, min_sources, min_domains, min_source_chars
            )

            if failed_urls or skipped_sources:
                log_event(
                    logger,
                    logging.WARNING,
                    "ingest_signals_degraded",
                    task_type=task_type,
                    profile_id=resolved_profile_id,
                    configured_count=len(configured_urls),
                    used_count=len(used_urls),
                    distinct_domain_count=len(_distinct_domains(used_urls)),
                    failed_urls=failed_urls,
                    redaction_skipped_urls=[item["url"] for item in skipped_sources],
                )

            output = _parse_json_content(response["content"])
            core._validate_function_output(function_id, output)
            _assert_signal_domain_floor(output, min_domains, len(_distinct_domains(used_urls)))

            repeat_count = _count_repeats(output, captured)
            if repeat_count:
                log_event(
                    logger,
                    logging.WARNING,
                    "ingest_signals_repeats",
                    task_type=task_type,
                    profile_id=resolved_profile_id,
                    repeat_count=repeat_count,
                    signal_count=len(_batch_items(output)),
                    already_captured_count=len(captured),
                )

            signal = vault.create_signal(
                source=f"function-{function_id}",
                signal_type=CARD_BATCH_TYPE,
                payload=output,
                campaign_id=campaign_id,
                function_id=function_id,
            )
            vault.update_agent_run(
                agent_run["id"], status="succeeded", output_payload=output, completed_at=_now_iso()
            )

        db.set_result_ref(
            task_id,
            {
                "status": "scanned",
                "vault_signal_id": signal["id"],
                "agent_run_id": agent_run["id"],
                "campaign_id": campaign_id,
                "topic": sources["topic"],
                "profile_id": resolved_profile_id,
                "function_id": function_id,
                "card_count": len(_batch_items(output)),
                "sources_configured": len(configured_urls),
                "sources_used": len(used_urls),
                "repeat_count": repeat_count,
            },
        )
        db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
        db.advance_dependents(task_id)

    handler.__name__ = f"{task_type.replace('-', '_')}_handler"
    return handler


SCANNER_HANDLERS = {
    task_type: _make_scanner_handler(task_type, function_id, profile_id, agent_name)
    for task_type, (function_id, profile_id, agent_name) in SCANNER_TASKS.items()
}


