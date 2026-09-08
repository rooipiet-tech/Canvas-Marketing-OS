from __future__ import annotations

import hashlib
import json
import logging
from typing import Any
from urllib.parse import urlparse

import yaml
from telemetry_lib import set_span_attribute

from orchestrator.config import functions_dir
from orchestrator.dispatch_errors import DispatchError
from orchestrator.logging_config import log_event, sanitize_exception_text
from orchestrator.models import TaskEnvelope, TaskStateEnum, TransitionReason
from orchestrator.telemetry_wiring import emit_task_span

from .. import clients, core, scan_shared
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
from ..scan_shared import PROBE_BATCH_TYPE

# ---------------------------------------------------------------------
# probe-sources — the source promotion pipeline (F-SOURCE-DISCOVERY)
# ---------------------------------------------------------------------
#
# Eleven scanner profiles ship without urls because nobody has written
# down where to read each sector, and the obvious fix -- let the system
# find its own sources -- runs straight into a circularity: a candidate
# cannot be evaluated without fetching it, and fetching it requires
# allow-listing, which is the decision the evaluation exists to inform.
#
# The pipeline resolves that by splitting the capability in two.
# mcp-web's probe_url reads MCP_WEB_PROBE_ALLOWLIST -- a different list
# from the production egress allow-list fetch_url uses -- and returns
# only SHAPE: status, content type, whether it parses as a feed, item
# count, extractable text size, and up to five item titles. Never the
# body. Probing is therefore a strictly smaller capability than scanning,
# so granting it is a smaller decision.
#
# Scoring is deterministic, for the same reason score-signals is: a
# source is judged on measurable properties, and a model asked "is this a
# good source?" would be inventing an opinion where arithmetic will do.
#
# PROMOTION IS NEVER AUTOMATIC. The handler ends by raising a real
# gate-check against config.source_promotion (autonomy level 1, one human
# approver) carrying the full probe detail and the reasoning as
# evidence_summary. Nothing here edits scan-profiles.yaml or the Bicep;
# a person reads the card and makes that edit. The egress allow-list is
# AC-17's security control, and a pipeline that could widen it unattended
# would be a pipeline that lets discovered content decide what the system
# may reach.

FUNCTION_ID_SOURCE_PROMOTION = "config.source_promotion"
SOURCE_PROMOTION_ACTION_CLASS = "configure"
SOURCE_CANDIDATES_PATH = ("_shared", "source-candidates.yaml")

# Score thresholds. A candidate at or above PROMOTE_SCORE is recommended;
# below REJECT_SCORE it is recommended against. Between them the card says
# "needs a human eye" rather than pretending the arithmetic decided.
PROMOTE_SCORE = 0.6
REJECT_SCORE = 0.3

# Minimum extractable text for a source to be worth a scan's evidence
# budget at all -- below this the daily scan would spend a fetch on
# nothing. Calibrated against the market-intelligence profile's own live
# feeds, which clear it comfortably.
MIN_USEFUL_EXTRACTABLE_CHARS = 500


def _load_source_candidates() -> list[dict[str, Any]]:
    path = functions_dir().joinpath(*SOURCE_CANDIDATES_PATH)
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    return list(document.get("candidates") or [])


def _score_probe(probe: dict[str, Any]) -> tuple[float, list[str]]:
    """Score a probe result 0..1, with the reasons that produced it.

    The reasons are the point: they go on the approval card verbatim, so a
    reviewer sees WHY a number came out the way it did rather than being
    asked to trust it. Every component is something the probe measured."""
    reasons: list[str] = []
    status = int(probe.get("status_code") or 0)
    if status != 200:
        return 0.0, [f"returned HTTP {status or 'no response'} — not reachable"]

    score = 0.4
    reasons.append("reachable (HTTP 200)")

    if probe.get("is_feed"):
        score += 0.2
        item_count = int(probe.get("item_count") or 0)
        reasons.append(f"parses as a feed with {item_count} item(s)")
        if item_count >= 5:
            score += 0.1
            reasons.append("carries enough items for a daily scan to find movement")
        else:
            reasons.append("few items — may go quiet between scans")
    else:
        reasons.append("not a feed — a page scan re-reads the same content until it changes")

    extractable = int(probe.get("extractable_chars") or 0)
    if extractable >= MIN_USEFUL_EXTRACTABLE_CHARS:
        score += 0.2
        reasons.append(f"{extractable} characters of extractable text")
    else:
        reasons.append(
            f"only {extractable} characters of extractable text — "
            f"below the {MIN_USEFUL_EXTRACTABLE_CHARS} a scan needs to attribute anything"
        )

    if probe.get("sample_titles"):
        score += 0.1
        reasons.append(f"sample titles retrieved for review ({len(probe['sample_titles'])})")
    else:
        reasons.append("no item titles found — a reviewer cannot see what this source carries")

    return round(min(score, 1.0), 2), reasons


def _promotion_verdict(score: float) -> str:
    if score >= PROMOTE_SCORE:
        return "recommend_promote"
    if score < REJECT_SCORE:
        return "recommend_reject"
    return "needs_review"


def _render_promotion_evidence(results: list[dict[str, Any]]) -> str:
    """The detail list and reasoning that goes on the approval card.

    Written for a person deciding whether to widen an egress allow-list,
    so it leads with what they are being asked to allow, states the
    machine's recommendation and why, and shows sample titles as evidence
    of what the source actually carries."""
    lines = [
        f"{len(results)} candidate source(s) probed in the sandbox "
        "(metadata only — no content was fetched into any scan).",
        "",
        "Approving this card authorises adding the recommended hosts to "
        "mcp-web's production egress allow-list and to their scan profile.",
        "",
    ]
    for result in results:
        probe = result["probe"]
        lines.append(f"— {result['candidate_id']} → profile {result['profile_id']}")
        lines.append(f"  url: {result['url']}")
        lines.append(f"  host: {result['host']}")
        lines.append(f"  score: {result['score']} → {result['verdict'].replace('_', ' ')}")
        lines.append(f"  why: {'; '.join(result['reasons'])}")
        if result.get("rationale"):
            lines.append(f"  proposed because: {result['rationale']}")
        titles = probe.get("sample_titles") or []
        if titles:
            lines.append("  sample titles:")
            lines += [f"    · {title}" for title in titles[:5]]
        lines.append("")
    return "\n".join(lines).strip()


# ---------------------------------------------------------------------
# propose-sources — function 17, the other half of source discovery
# ---------------------------------------------------------------------
#
# The probe pipeline could test candidates but only a human could think of
# them, so eleven profiles stayed empty. Function 17 proposes addresses
# from its own knowledge for any profile that has none.
#
# IT IS PERMITTED NOTHING (see its tools.yaml). No fetch, no search, no
# Vault read: giving a proposer retrieval would let a model's own
# suggestion cause an outbound request to an arbitrary host, which is the
# circularity the sandboxed probe exists to break. It reasons from the
# profile's topic and watchlist prose and returns a hypothesis.
#
# AND A PROPOSAL CANNOT REACH THE PROBE ON ITS OWN. probe_url reads
# MCP_WEB_PROBE_ALLOWLIST, so a host nobody has cleared is unprobeable --
# by design, and it means machine-proposed hosts need a human decision
# BEFORE they can even be measured. That is a second, smaller gate in
# front of the promotion gate, and the smaller one is the more important:
# it is the one that stops a model's guess from causing a network call.
#
# So this handler ends where every other risky path here ends: a
# gate-check card, carrying each proposed host, who publishes it, why it
# was proposed and how sure the model is that the address even exists.
# Approving it authorises adding those hosts to the PROBE allow-list only
# -- never the scan one, which still requires the probe evidence and the
# second card.

FUNCTION_ID_17 = "17-source-scout"
PROPOSAL_BATCH_TYPE = "source_proposal_batch"


def _profiles_needing_sources() -> list[dict[str, Any]]:
    """Every profile with no urls, defaults merged. These are exactly the
    scanners completing as not_configured every morning."""
    document = scan_shared._load_scan_profiles()
    defaults = document.get("defaults", {})
    return [
        {**defaults, **profile}
        for profile in document.get("profiles", [])
        if not profile.get("urls")
    ]


def _known_candidate_urls(profile_id: str) -> list[str]:
    """What the register already holds for this profile, so the scout is
    told not to re-propose it."""
    return [
        str(candidate.get("url", ""))
        for candidate in _load_source_candidates()
        if candidate.get("profile_id") == profile_id
    ]


def _render_proposal_evidence(proposals: list[dict[str, Any]]) -> str:
    """The detail list for the probe-allow-list card.

    A reviewer is being asked to let an automated step make outbound
    requests to these hosts, so the card leads with that, and shows the
    model's own confidence that each address exists at all -- which is the
    honest measure of how much of this list is likely to be wrong."""
    hosts = sorted({item["host"] for item in proposals if item["host"]})
    lines = [
        f"{len(proposals)} candidate source(s) proposed by function 17 across "
        f"{len({item['profile_id'] for item in proposals})} unsourced profile(s).",
        "",
        "Nothing has been fetched. Function 17 has no retrieval tools at all — "
        "these are addresses it believes exist, not addresses anyone has tested.",
        "",
        "Approving this card authorises adding these hosts to mcp-web's PROBE "
        "allow-list, so the weekly probe may fetch their metadata (status, feed "
        "shape, item count, sample titles — never the body). It does NOT put them "
        "on the scan allow-list: that needs the probe evidence and a second card.",
        "",
        f"Hosts: {', '.join(hosts) if hosts else '(none)'}",
        "",
    ]
    for item in proposals:
        lines.append(f"— {item['url']}")
        lines.append(f"  profile: {item['profile_id']}  ·  publisher: {item['publisher']}")
        lines.append(f"  kind: {item['source_kind']}  ·  address confidence: {item['confidence']}")
        lines.append(f"  why: {item['rationale']}")
        lines.append("")
    return "\n".join(lines).strip()


def propose_sources_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    profiles = _profiles_needing_sources()
    if not profiles:
        log_event(logger, logging.INFO, "no_profiles_need_sources")
        db.set_result_ref(task_id, {"status": "nothing_to_propose", "proposal_count": 0})
        db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
        db.advance_dependents(task_id)
        return

    proposals: list[dict[str, Any]] = []
    with clients.build_vault_client() as vault:
        campaign_id = vault.get_or_create_campaign(
            _campaign_name(envelope), function_id=FUNCTION_ID_17
        )
        agent_run = vault.create_agent_run_idempotent(
            task_id=task_id,
            db=db,
            agent_name=_agent_name("source-scout", envelope),
            campaign_id=campaign_id,
            function_id=FUNCTION_ID_17,
            status="running",
            input_payload={"profile_count": len(profiles)},
        )
        system_prompt = _read_prompt(FUNCTION_ID_17)

        with emit_task_span(
            "propose-sources",
            function_id=FUNCTION_ID_17,
            task_ref=task_id,
            model="claude-sonnet",
            run_id=str(envelope.campaign_id),
        ) as span:
            total_cost = 0.0
            with clients.build_gateway_client() as gateway:
                for profile in profiles:
                    payload = {
                        "profile_id": profile["profile_id"],
                        "topic": profile["topic"],
                        "watchlist_note": profile.get("watchlist_note", ""),
                        "existing_urls": list(profile.get("urls") or []),
                        "existing_candidates": _known_candidate_urls(profile["profile_id"]),
                    }
                    _validate_function_input(FUNCTION_ID_17, payload)
                    response, cost = _complete_and_meter(
                        gateway,
                        vault,
                        model="claude-sonnet",
                        system_prompt=system_prompt,
                        user_content=json.dumps(payload),
                        agent_run_id=agent_run["id"],
                    )
                    total_cost += cost
                    output = _parse_json_content(response["content"])
                    core._validate_function_output(FUNCTION_ID_17, output)
                    for candidate in output.get("candidates", []):
                        url = str(candidate.get("url", ""))
                        proposals.append(
                            {
                                "profile_id": profile["profile_id"],
                                "url": url,
                                "host": (urlparse(url).hostname or "").lower(),
                                "publisher": str(candidate.get("publisher", "")),
                                "source_kind": str(candidate.get("source_kind", "")),
                                "rationale": str(candidate.get("rationale", "")),
                                "confidence": str(candidate.get("confidence", "")),
                            }
                        )
            set_span_attribute(span, "cost", total_cost)

        proposal_batch = vault.create_signal(
            source="source-scout-pipeline",
            signal_type=PROPOSAL_BATCH_TYPE,
            payload={"proposals": proposals},
            campaign_id=campaign_id,
            function_id=FUNCTION_ID_17,
        )
        vault.update_agent_run(
            agent_run["id"],
            status="succeeded",
            output_payload={"proposal_count": len(proposals)},
            completed_at=_now_iso(),
        )

    evidence = _render_proposal_evidence(proposals)
    content_hash = hashlib.sha256(
        json.dumps(
            sorted({item["host"] for item in proposals if item["host"]}),
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    with clients.build_gatekeeper_client() as gatekeeper:
        decision = gatekeeper.gate_check(
            agent_run_id=str(agent_run["id"]),
            function_id=FUNCTION_ID_SOURCE_PROMOTION,
            action_class=SOURCE_PROMOTION_ACTION_CLASS,
            content_hash=content_hash,
            preview_title=(
                f"Probe allow-list — {len({item['host'] for item in proposals})} host(s) "
                f"proposed for {len(profiles)} unsourced profile(s)"
            ),
            preview_reference=f"proposal-batch://{proposal_batch['id']}",
            evidence_summary=evidence,
        )

    db.set_result_ref(
        task_id,
        {
            "status": "proposed",
            "proposal_batch_id": proposal_batch["id"],
            "agent_run_id": agent_run["id"],
            "campaign_id": campaign_id,
            "profile_count": len(profiles),
            "proposal_count": len(proposals),
            "proposed_hosts": sorted({item["host"] for item in proposals if item["host"]}),
            "decision_id": decision.get("decision_id"),
            "outcome": decision.get("outcome"),
            "content_hash": content_hash,
        },
    )
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)


def probe_sources_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    candidates = _load_source_candidates()
    if not candidates:
        raise DispatchError(
            f"probe-sources: functions/{'/'.join(SOURCE_CANDIDATES_PATH)} lists no candidates"
        )

    results: list[dict[str, Any]] = []
    with clients.build_mcp_web_client() as mcp:
        for candidate in candidates:
            url = str(candidate.get("url", ""))
            try:
                probe = mcp.call_tool("probe_url", {"url": url})
            except Exception as exc:  # noqa: BLE001 - one unreachable candidate is a RESULT
                log_event(
                    logger,
                    logging.WARNING,
                    "probe_url_failed",
                    url=url,
                    error=sanitize_exception_text(exc),
                )
                probe = {"status_code": 0, "error": "probe failed"}
            score, reasons = _score_probe(probe)
            results.append(
                {
                    "candidate_id": candidate.get("candidate_id"),
                    "profile_id": candidate.get("profile_id"),
                    "url": url,
                    "host": (urlparse(url).hostname or "").lower(),
                    "rationale": candidate.get("rationale"),
                    "score": score,
                    "verdict": _promotion_verdict(score),
                    "reasons": reasons,
                    "probe": probe,
                }
            )

    results.sort(key=lambda item: -item["score"])
    recommended = [item for item in results if item["verdict"] == "recommend_promote"]

    with clients.build_vault_client() as vault:
        campaign_id = vault.get_or_create_campaign(
            _campaign_name(envelope), function_id=FUNCTION_ID_SOURCE_PROMOTION
        )
        agent_run = vault.create_agent_run_idempotent(
            task_id=task_id,
            db=db,
            agent_name=_agent_name("source-promotion-scout", envelope),
            campaign_id=campaign_id,
            function_id=FUNCTION_ID_SOURCE_PROMOTION,
            status="running",
            input_payload={"candidate_count": len(candidates)},
        )
        probe_batch = vault.create_signal(
            source="source-promotion-pipeline",
            signal_type=PROBE_BATCH_TYPE,
            payload={"results": results},
            campaign_id=campaign_id,
            function_id=FUNCTION_ID_SOURCE_PROMOTION,
        )
        vault.update_agent_run(
            agent_run["id"],
            status="succeeded",
            output_payload={
                "probed": len(results),
                "recommended": [item["candidate_id"] for item in recommended],
            },
            completed_at=_now_iso(),
        )

    evidence = _render_promotion_evidence(results)
    content_hash = hashlib.sha256(
        json.dumps(
            [
                {"candidate_id": item["candidate_id"], "host": item["host"], "score": item["score"]}
                for item in results
            ],
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    with clients.build_gatekeeper_client() as gatekeeper:
        with emit_task_span(
            "probe-sources",
            function_id=FUNCTION_ID_SOURCE_PROMOTION,
            task_ref=task_id,
            model="none",
            cost=0.0,
            run_id=str(envelope.campaign_id),
        ):
            decision = gatekeeper.gate_check(
                agent_run_id=str(agent_run["id"]),
                function_id=FUNCTION_ID_SOURCE_PROMOTION,
                action_class=SOURCE_PROMOTION_ACTION_CLASS,
                content_hash=content_hash,
                preview_title=(
                    f"Source promotion — {len(recommended)} of {len(results)} candidate(s) "
                    "recommended for the scan allow-list"
                ),
                preview_reference=f"probe-batch://{probe_batch['id']}",
                evidence_summary=evidence,
            )

    db.set_result_ref(
        task_id,
        {
            "probe_batch_id": probe_batch["id"],
            "agent_run_id": agent_run["id"],
            "campaign_id": campaign_id,
            "probed_count": len(results),
            "recommended_candidate_ids": [item["candidate_id"] for item in recommended],
            "decision_id": decision.get("decision_id"),
            "outcome": decision.get("outcome"),
            "approval_id": decision.get("approval_id"),
            "content_hash": content_hash,
        },
    )
    # Completes as soon as /gate-check responds -- the human decision
    # arrives asynchronously on the approval surface, exactly as
    # request-approval does. Nothing downstream edits config either way:
    # promotion is a person editing scan-profiles.yaml and main.bicep.
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)


