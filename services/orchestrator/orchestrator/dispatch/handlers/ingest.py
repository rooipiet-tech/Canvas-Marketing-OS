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
    FUNCTION_ID_09,
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
    QUIET_SCAN_STATUS,
    SIGNAL_BATCH_TYPE,
    _assert_ingest_floor,
    _assert_signal_domain_floor,
    _batch_items,
    _count_repeats,
    _distinct_domains,
    _envelope_scan_profile_id,
    _ingest_floors,
    _ingest_min_source_chars,
    _ingest_source_chars,
)

# ---------------------------------------------------------------------
# ingest-signals (plan step 7; AC-01, AC-24, AC-28)
# ---------------------------------------------------------------------

# F-A / F-E (scan-market fixes, this change). Two gaps this section closes,
# both of which previously let a scan REPORT success while producing
# something that could not satisfy its own stated contract:
#
#   F-A -- the model's output was json.loads'd and written straight to the
#          Vault. functions/09-market-intelligence-director/schema.json
#          existed and was correct, but nothing called it at runtime: the
#          "at least 3 signals, https attribution, five-pillar enum,
#          honest confidence" contract was prompt text plus CI evals
#          against a deterministic MOCK. A live run returning one
#          unattributed signal wrote a signals row and COMPLETED.
#   F-E -- a failed fetch was a warning and a redaction block dropped a
#          source, so the task succeeded on ONE surviving source, which
#          structurally cannot satisfy prompt.md's own "at least 2
#          distinct domains" rule. Nothing recorded that the day's scan
#          had run on 1 of 4 sources.
#
# Both now fail closed, consistent with this codebase's existing default
# (unlisted autonomy pair -> blocked; unlisted client -> blocked; failed
# Vault lookup -> refuse). A scan that cannot meet its contract must not
# write a signals row that downstream briefs will cite as evidence.


INGEST_ORDINARY_SIGNAL_COUNT = 3


def ingest_signals_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    sources = scan_shared._resolve_scan_profile(_envelope_scan_profile_id(envelope))
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
            except Exception as exc:  # noqa: BLE001 - one bad source must not sink the whole scan
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
            f"ingest-signals: every source configured for scan profile "
            f"{sources['profile_id']!r} failed to fetch"
        )

    # Checked BEFORE the model call, so a scan that already cannot meet its
    # contract costs nothing to fail (F-E). The thin sources it drops are
    # NOT removed from `fetched` -- the model still sees them, exactly as
    # it would a short-but-real page; they simply stop counting toward the
    # floors, which is the whole of F-INGEST-CONTENT-FLOOR.
    _assert_ingest_floor("retrieval", fetched, min_sources, min_domains, min_source_chars)

    with clients.build_vault_client() as vault:
        campaign_id = vault.get_or_create_campaign(
            _campaign_name(envelope), function_id=FUNCTION_ID_09
        )
        agent_run = vault.create_agent_run_idempotent(
            task_id=task_id,
            db=db,
            agent_name=_agent_name("market-intelligence-director", envelope),
            campaign_id=campaign_id,
            function_id=FUNCTION_ID_09,
            status="running",
            input_payload={
                "topic": sources["topic"],
                "horizon_days": sources["horizon_days"],
                "source_urls": [item["url"] for item in fetched],
                # Recorded alongside the fetched set so the Vault -- not
                # only a log line somebody has to go looking for -- carries
                # how complete this scan actually was (F-E).
                "source_urls_configured": configured_urls,
                "scan_profile_id": sources["profile_id"],
                "proof_circuit_tag": PROOF_CIRCUIT_TAG if is_proof_circuit(envelope) else None,
            },
        )

        system_prompt = _read_prompt("09-market-intelligence-director")
        captured = scan_shared._already_captured(vault, sources)

        with emit_task_span(
            "ingest-signals",
            function_id=FUNCTION_ID_09,
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
            if skipped_sources:
                log_event(
                    logger,
                    logging.WARNING,
                    "ingest_signals_sources_redacted",
                    skipped_urls=[item["url"] for item in skipped_sources],
                    used_urls=[item["url"] for item in used_sources],
                )

        used_urls = [item["url"] for item in used_sources]

        # Re-checked AFTER the redaction fallback, which drops sources one
        # at a time and can take a set that passed the retrieval check
        # below the floor (F-E). Redaction also SHORTENS bodies, so this
        # is the check that catches a source redacted down to nothing.
        _assert_ingest_floor(
            "the redaction fallback", used_sources, min_sources, min_domains, min_source_chars
        )

        if failed_urls or skipped_sources:
            # One grep-able line stating exactly how complete the day's
            # scan was. Emitted only when something was actually lost, so
            # its presence in the log IS the signal.
            log_event(
                logger,
                logging.WARNING,
                "ingest_signals_degraded",
                configured_count=len(configured_urls),
                used_count=len(used_urls),
                distinct_domain_count=len(_distinct_domains(used_urls)),
                failed_urls=failed_urls,
                redaction_skipped_urls=[item["url"] for item in skipped_sources],
            )

        output = _parse_json_content(response["content"])
        # F-A: schema.json is the contract, so make it the contract at
        # runtime and not only in CI against a mock.
        #
        # F-INGEST-EMPTY-SCAN (live, deploy-loop-e2e-smoke #121): this
        # raised "at signals (1 violation(s)): [] is too short" three
        # times and dead-lettered all 20 tasks in the daily loop, with
        # nothing in the log saying what the scan had been given. The one
        # line carrying already_captured_count is ingest_signals_repeats
        # below -- AFTER this call -- so on the failure path it never
        # fired. That matters because the two plausible causes want
        # opposite fixes: a genuinely thin retrieval (evidence problem)
        # versus the exclusion list crowding out everything the model
        # would otherwise report (memory problem).
        #
        # F-INGEST-QUIET-SCAN. The deeper reason an empty batch was
        # reachable at all is that the function asked for two
        # contradictory things. prompt.md hard rule 1 said "return at
        # least 3" and schema.json enforced minItems 3, while hard rule 9
        # said "never pad the batch back up to the minimum... a scan that
        # honestly found little is more useful than one that restates last
        # week" -- and _build_ingest_user_content repeats that as "do not
        # pad to reach the minimum". On a day yielding fewer than three
        # attributable NEW signals the model could only pad (breaking rule
        # 9, and rule 2 for anything unattributed) or fall short (failing
        # the schema, dead-lettering the task and cascading to all 13
        # descendants). The system failed the scan for telling the truth.
        #
        # minItems is now 1 and rule 1 asks for "3 to 8 on an ordinary
        # day", so a short honest batch is a valid answer that still
        # writes its signals row and lets the loop run.
        #
        # This is only safe because F-INGEST-CONTENT-FLOOR landed first.
        # Relaxing the floor on its own would have made a genuinely quiet
        # market indistinguishable from a broken retrieval -- which is
        # precisely the confusion that cost three weeks, since the
        # 176-byte-fixture outage presented as an empty batch too. The
        # content floor fails a stub scan at retrieval, BEFORE the model
        # call, so by the time a short batch is being judged here the
        # evidence behind it has already been shown to be real.
        #
        # A short batch is still reported, at WARNING, because "quiet" is
        # a claim about the market that deserves to be checkable against
        # the evidence counts that produced it.
        try:
            core._validate_function_output(FUNCTION_ID_09, output)
        except Exception:
            log_event(
                logger,
                logging.ERROR,
                "ingest_signals_output_rejected",
                profile_id=sources["profile_id"],
                emitted_signal_count=len(_batch_items(output)),
                already_captured_count=len(captured),
                used_count=len(used_urls),
                distinct_domain_count=len(_distinct_domains(used_urls)),
                evidence_chars=sum(len(item["body"] or "") for item in used_sources),
                redaction_skipped_count=len(skipped_sources),
            )
            raise
        _assert_signal_domain_floor(output, min_domains, len(_distinct_domains(used_urls)))

        emitted_count = len(_batch_items(output))
        if emitted_count < INGEST_ORDINARY_SIGNAL_COUNT:
            # Carries the same evidence counts as the rejection diagnostic
            # above, so "the market was quiet" can be checked against what
            # the scan was actually given rather than taken on trust.
            log_event(
                logger,
                logging.WARNING,
                "ingest_signals_quiet_scan",
                profile_id=sources["profile_id"],
                emitted_signal_count=emitted_count,
                ordinary_signal_count=INGEST_ORDINARY_SIGNAL_COUNT,
                already_captured_count=len(captured),
                used_count=len(used_urls),
                distinct_domain_count=len(_distinct_domains(used_urls)),
                evidence_chars=sum(len(item["body"] or "") for item in used_sources),
            )

        repeat_count = _count_repeats(output, captured)
        if repeat_count:
            log_event(
                logger,
                logging.WARNING,
                "ingest_signals_repeats",
                profile_id=sources["profile_id"],
                repeat_count=repeat_count,
                signal_count=len(_batch_items(output)),
                already_captured_count=len(captured),
            )

        signal = vault.create_signal(
            source="function-09-market-intelligence-director",
            signal_type=SIGNAL_BATCH_TYPE,
            payload=output,
            campaign_id=campaign_id,
            function_id=FUNCTION_ID_09,
        )
        vault.update_agent_run(
            agent_run["id"], status="succeeded", output_payload=output, completed_at=_now_iso()
        )

    db.set_result_ref(
        task_id,
        {
            "vault_signal_id": signal["id"],
            "agent_run_id": agent_run["id"],
            "campaign_id": campaign_id,
            "topic": sources["topic"],
            # Additive keys (result_ref is untyped JSONB): an operator
            # reading /status sees a degraded-but-passing scan for what it
            # is, instead of an unqualified "completed" (F-E).
            "sources_configured": len(configured_urls),
            "sources_used": len(used_urls),
            "scan_profile_id": sources["profile_id"],
            # How much of today's batch restates something already in the
            # Vault inside this horizon. Measured, not filtered -- see
            # _already_captured's own note on why.
            "repeat_count": repeat_count,
            "already_captured_count": len(captured),
            "signal_count": emitted_count,
        }
        # F-INGEST-QUIET-ZERO. A zero-signal batch is now valid output
        # (schema.json minItems 0), and this is what tells the brief chain
        # to stand down instead of each stage discovering an empty batch
        # for itself. The scanners hanging off `ingest` never read this
        # key, so they keep running -- which is the whole point of
        # completing rather than failing here.
        #
        # Only set on a genuine zero. A short batch is a normal answer and
        # must stay indistinguishable from any other completed scan to
        # everything downstream; it is already reported by
        # ingest_signals_quiet_scan at WARNING.
        | ({"status": QUIET_SCAN_STATUS} if emitted_count == 0 else {}),
    )
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)

