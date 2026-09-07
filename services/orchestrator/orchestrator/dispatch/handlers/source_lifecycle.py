from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import urlparse

import yaml
from options_inbox.cards import build_card
from telemetry_lib import set_span_attribute

from orchestrator.clients.vault_client_ext import VaultClientExt
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
from ..scan_shared import PROBE_BATCH_TYPE, _parse_iso_timestamp

# ---------------------------------------------------------------------
# Fn 128 -- Source Discovery & Lifecycle Manager (Appendix D PR 5b)
# ---------------------------------------------------------------------
#
# ABSORBS 17-source-scout (v3 Appendix A / functions/128's own prompt.md
# header). FUNCTION_ID_17's propose_sources_handler/probe_sources_handler
# above predate OptionCards entirely -- they end in a generic
# gatekeeper.gate_check, never a source.promote card -- and neither
# "propose-sources" nor "probe-sources" appears in any shipped loop
# (confirmed: absent from daily-signal-loop.yaml, the only loop that
# could plausibly carry them). They were never wired, so there is
# nothing live to migrate off of; left in place, untouched, as dead but
# harmless code (AC-02 -- nothing here regresses an already-shipped
# path). source-lifecycle-loop.yaml below is what actually runs daily
# going forward, and is the real replacement.
#
# SCOPE CUT, DOCUMENTED. prompt.md's task step 1 describes live "reach
# channels" -- Claude web research and Semrush -- for daily NEW-candidate
# discovery. Neither is wired anywhere: a repo-wide grep for a
# `web_search`-shaped tool-use loop in model-gateway or this file returns
# nothing -- every existing handler (including this one) is a single-shot
# completion, never an agentic tool loop. Building that is a
# model-gateway-level capability change, not a Fn 128 wiring change, and
# Appendix D PR 5c is explicitly where the real reach mechanism
# (discovery API + crawler, governed by Fn 129) lands. So this PR wires
# the CARD MECHANISM -- dedupe, scoring, card build, nightly yield,
# monthly retire, 30-day provisional expiry -- against the ALREADY-
# PROBED alternate candidates functions/_shared/source-candidates.
# bootstrap.yaml's own 4 Sep 2026 research pass recorded (every one a
# real fetch) but did not choose for PR 5a. Live daily NEW discovery
# beyond that one-time haul is deferred to PR 5c, same as the reach
# tools it depends on.

FUNCTION_ID_128 = "128-source-discovery-lifecycle"
BOOTSTRAP_CANDIDATES_PATH = ("_shared", "source-candidates.bootstrap.yaml")

RETIRED_SOURCE_SIGNAL_TYPE = "source_retired"
YIELD_SIGNAL_TYPE = "source_yield"
RETIRE_PASS_MARKER_TYPE = "source_retire_pass_marker"

# Vault /signals has no server-side filter (VaultClientExt.list_signals'
# own docstring); the daily loop writes ~6+ signals/day for this function
# alone (5 discovery classes + 1 yield sweep), so a 28-day lookback needs
# a wider page than the 100-200 other handlers' cross-run memory uses.
LIFECYCLE_SIGNAL_LOOKBACK = 500

# v3 §11.2's five signal classes -> functions/_shared/source-candidates.
# bootstrap.yaml's own profile_keys. Several blueprint functions collapse
# into one broader class each (10/11/12/13 -> competitors; 14/15/16 ->
# the Fabric/Power BI class; 17/19 -> adjacent-tech/regulation; 20/21/22
# -> tenders/events/partners) -- the same collapsing the six vertical-
# intel packages already apply to industry-trends in scan-profiles.yaml.
# "reputation-community" has no dedicated profile_key: the bootstrap
# file's own trailing note says that class is query-driven (a Semrush
# brand-mention search), not URL-driven, so it stays empty here until PR
# 5c wires a query-capable reach tool.
SIGNAL_CLASS_BOOTSTRAP_PROFILE_KEYS: dict[str, list[str]] = {
    "competitors": ["competitor-discovery", "competitor-change", "competitor-content"],
    "microsoft-fabric-power-bi": ["pricing-packaging", "new-product-scout", "microsoft-ecosystem"],
    "adjacent-technology-industry-trends-regulation": [
        "adjacent-technology",
        "industry-trends",
        "regulatory",
    ],
    "tenders-events-partners": ["tenders-rfp", "events-conferences", "partner-channel"],
    "reputation-community": [],
}

# scan-profiles.yaml's own twelve profile_ids -> the signal_class each
# belongs to, for the monthly retire pass's replacement search (the live
# profile carries no signal_class field itself). Hand-authored against
# each profile's own topic/bootstrap comment, not derived -- see each
# profile's own header in scan-profiles.yaml.
SCAN_PROFILE_SIGNAL_CLASS: dict[str, str] = {
    "market-intelligence": "microsoft-fabric-power-bi",
    "competitor-discovery": "competitors",
    "competitor-change": "competitors",
    "competitive-positioning": "competitors",
    "competitor-content-performance": "competitors",
    "fabric-ecosystem": "microsoft-fabric-power-bi",
    "vertical-logistics-fleet": "adjacent-technology-industry-trends-regulation",
    "vertical-mining-industrial": "adjacent-technology-industry-trends-regulation",
    "vertical-manufacturing": "adjacent-technology-industry-trends-regulation",
    "vertical-construction": "adjacent-technology-industry-trends-regulation",
    "vertical-fmcg-beverage": "adjacent-technology-industry-trends-regulation",
    "vertical-financial-services": "adjacent-technology-industry-trends-regulation",
}

# Monthly retire pass self-gating (report_month_end_handler has the same
# unresolved "no external monthly trigger exists" gap -- see its own
# history; this handler runs inside the daily loop like every other
# source-lifecycle task, but no-ops unless this many days have actually
# passed since its own last real pass).
RETIRE_PASS_MIN_DAYS = 28
# "yield has fallen below floor" (prompt.md task step 6), scope-cut to
# what source_yield_handler actually measures (reachability only -- see
# its own docstring): a source is below floor once its last N nightly
# checks are ALL unreachable.
YIELD_FLOOR_CONSECUTIVE_FAILURES = 5

_LAST_ITEM_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _freshness_days(last_item: str, *, as_of: date) -> int:
    """Days between the bootstrap file's recorded `last_item` and now.
    Returns a large sentinel (never a small fabricated number) when no
    parseable date is present, e.g. "n/a (JS)" or "next 2026-09-17; last
    2026-08-27" (the LAST match, not the first, since several of these
    strings lead with an upcoming date)."""
    matches = _LAST_ITEM_DATE_RE.findall(last_item or "")
    if not matches:
        return 999
    try:
        found = date.fromisoformat(matches[-1])
    except ValueError:
        return 999
    return max((as_of - found).days, 0)


def _forecast_yield_from_cadence(cadence: str) -> float:
    """Rough, deterministic weekly-yield estimate from the bootstrap
    file's free-text `cadence` field -- not a live measurement (PR 5c's
    discovery API/crawler is what turns this into a real count), just
    enough signal to rank a recommendation. Unrecognised text defaults to
    a conservative ~monthly rate rather than 0, which would make an
    unparsed cadence read identically to `dormant`."""
    text = (cadence or "").lower()
    if "dormant" in text:
        return 0.0
    if "continuous" in text or "daily" in text:
        return 7.0
    if "week" in text:
        return 3.0 if ("several" in text or "multiple" in text) else 1.0
    if "month" in text:
        return 0.75 if any(char.isdigit() for char in text) else 0.25
    if "quarter" in text:
        return 0.08
    if "year" in text:
        return 0.02
    if "sporadic" in text or "batch" in text:
        return 0.1
    return 0.2


def _load_bootstrap_document() -> dict[str, Any]:
    path = functions_dir().joinpath(*BOOTSTRAP_CANDIDATES_PATH)
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _live_source_urls() -> set[str]:
    """Every URL already on some scan profile, across all twelve -- the
    dedupe boundary a discovered candidate must clear before it ever
    reaches a card (prompt.md task step 2)."""
    document = scan_shared._load_scan_profiles()
    urls: set[str] = set()
    for profile in document.get("profiles", []):
        urls.update(str(url) for url in (profile.get("urls") or []))
    return urls


def _retired_source_urls(vault: VaultClientExt) -> set[str]:
    """URLs a prior source.retire pass has already flagged (written by
    source_retire_handler below, signal_type=RETIRED_SOURCE_SIGNAL_TYPE)
    -- excluded from future candidate pools so a retired source is never
    silently re-discovered under a fresh card the next day."""
    urls: set[str] = set()
    for row in vault.list_signals(limit=LIFECYCLE_SIGNAL_LOOKBACK):
        if row.get("signal_type") == RETIRED_SOURCE_SIGNAL_TYPE:
            url = (row.get("payload") or {}).get("url")
            if url:
                urls.add(str(url))
    return urls


def _client_domain_excluded(domain: str) -> bool:
    """prompt.md hard rule 2: a client domain is excluded before probing,
    never filtered after. docs/permission-register.yaml holds client
    NAMES, not domains -- it governs text mentions (see permission_check.
    py's own docstring) -- so this is a defensive substring check of the
    domain against every registered client name; the bootstrap file's own
    "no client names, ever" rule (competitors are named there, clients
    are not) is the primary control this data already passed once. This
    is a second, cheap gate over it, not a replacement for it."""
    permission_check = clients.load_permission_check()
    lowered = domain.lower()
    return any(name.lower() in lowered for name in permission_check.registered_names())


def _bootstrap_candidate_pool(
    signal_class: str, *, exclude_urls: set[str], as_of: date
) -> list[dict[str, Any]]:
    """Every option (recommended or not) and `also_verified` entry, across
    every profile_key this signal_class maps to, that is not already live
    and not already retired -- the raw material candidate_pool for Fn
    128's own model call. Every entry carries the bootstrap file's OWN
    recorded probe fields (robots/last_item/cadence/authority, every one
    an actual fetch on 4 Sep 2026 -- see that file's own header), never a
    fabricated or assumed value. `authority` is carried as an extra key
    (not part of the model's own input/output contract) purely so the
    caller can build a real evidence_refs.authority from it rather than
    trusting whatever the model echoes back -- see the two handlers
    below."""
    document = _load_bootstrap_document()
    profiles_by_key = {p["profile_key"]: p for p in document.get("profiles", [])}
    pool: list[dict[str, Any]] = []
    for profile_key in SIGNAL_CLASS_BOOTSTRAP_PROFILE_KEYS.get(signal_class, []):
        profile = profiles_by_key.get(profile_key)
        if profile is None:
            continue
        raw_candidates = list(profile.get("options") or []) + list(
            profile.get("also_verified") or []
        )
        for raw in raw_candidates:
            url = raw.get("url") or ""
            feed_url = raw.get("feed_url")
            if not url and isinstance(feed_url, str):
                url = feed_url
            elif not url and isinstance(feed_url, list) and feed_url:
                url = feed_url[0]
            url = str(url)
            if not url.startswith("https://") or url in exclude_urls:
                continue
            domain = (urlparse(url).hostname or "").lower()
            if not domain or _client_domain_excluded(domain):
                continue
            probe = raw.get("probe") or {}
            pool.append(
                {
                    "url": url,
                    "domain": domain,
                    "rationale": str(raw.get("name") or raw.get("rationale") or profile_key),
                    "provisional": True,
                    "authority": str(probe.get("authority") or "secondary"),
                    "probe": {
                        "reachable": probe.get("status") == 200,
                        "freshness_days": _freshness_days(
                            str(probe.get("last_item") or ""), as_of=as_of
                        ),
                        "robots_allows": bool(probe.get("robots")),
                        # These candidates were probed for the PR 5a research
                        # pass, not promoted by it -- only the profiles'
                        # `recommended` options went on the allow-list.
                        "on_allowlist": False,
                        "duplicate_rate": 0.0,
                        "forecast_yield_per_week": _forecast_yield_from_cadence(
                            str(probe.get("cadence") or "")
                        ),
                        "evidence_ref": f"bootstrap://{profile_key}",
                    },
                }
            )
    seen: set[str] = set()
    deduped = []
    for item in pool:
        if item["url"] in seen:
            continue
        seen.add(item["url"])
        deduped.append(item)
    return deduped[:9]  # a card needs <=3; 9 gives the model real room to choose


def _expired_provisional_urls(document: dict[str, Any], *, as_of: date) -> set[str]:
    """prompt.md task step 7 / hard rule: a Stage 0 provisional source not
    re-ratified within its own review_by date is eligible for the SAME
    retire-card treatment as a low-yield source (never a silent drop --
    hard rule 5)."""
    expired: set[str] = set()
    for profile in document.get("profiles", []):
        for entry in profile.get("provisional_sources") or []:
            review_by = entry.get("review_by")
            if not review_by:
                continue
            try:
                due = date.fromisoformat(str(review_by))
            except ValueError:
                continue
            if due < as_of:
                expired.add(str(entry.get("url")))
    return expired


def _source_lifecycle_options(
    candidates: list[dict[str, Any]], pool_by_url: dict[str, dict[str, Any]], *, task_type: str
) -> list[dict[str, Any]]:
    """Builds OptionCard `options` from the MODEL's chosen option_id/url/
    distinctness_axis/rationale, but every probe/authority FIELD comes
    from dispatch.py's own pool_by_url, never the model's echoed `probe`
    object -- the model's job is the judgment call (which candidate, what
    axis), not restating numbers it could fabricate (fabricated-proof-
    point guard, same principle as _option_evidence_refs elsewhere in
    this file)."""
    options = []
    for candidate in candidates:
        original = pool_by_url.get(candidate.get("url"))
        if original is None:
            raise DispatchError(
                f"{task_type}: model echoed a candidate url not in candidate_pool: "
                f"{candidate.get('url')!r}"
            )
        probe = original["probe"]
        options.append(
            {
                "option_id": candidate["option_id"],
                "label": original["domain"],
                "summary": f"{original['url']} — {original['rationale']}"[:400],
                "payload_ref": probe["evidence_ref"],
                "evidence_refs": [
                    {
                        "source_type": "web_source",
                        "ref": probe["evidence_ref"],
                        "quote": original["rationale"][:300],
                        "authority": original.get("authority", "secondary"),
                    }
                ],
                "predicted_outcome": (
                    f"~{probe['forecast_yield_per_week']:.1f} signal(s)/week forecast"
                ),
                "risks": (
                    []
                    if probe["robots_allows"]
                    else ["robots.txt does not explicitly permit this path"]
                ),
                "distinctness_axis": candidate["distinctness_axis"],
            }
        )
    return options


SOURCE_DISCOVERY_TASKS: dict[str, str] = {
    # task_type: signal_class
    "source-discovery-competitors": "competitors",
    "source-discovery-fabric-ecosystem": "microsoft-fabric-power-bi",
    "source-discovery-adjacent-industry-regulation": (
        "adjacent-technology-industry-trends-regulation"
    ),
    "source-discovery-tenders-events-partners": "tenders-events-partners",
    "source-discovery-reputation-community": "reputation-community",
}


def _make_source_discovery_handler(task_type: str, signal_class: str):
    """One of Fn 128's five daily per-class discovery tasks (prompt.md
    task steps 1-4). See the module-section docstring above for the
    documented scope cut this runs against."""

    def handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
        with clients.build_vault_client() as vault:
            campaign_id = vault.get_or_create_campaign(
                _campaign_name(envelope), function_id=FUNCTION_ID_128
            )
            exclude_urls = _live_source_urls() | _retired_source_urls(vault)
            candidate_pool = _bootstrap_candidate_pool(
                signal_class,
                exclude_urls=exclude_urls,
                as_of=datetime.now(timezone.utc).date(),
            )

            if not candidate_pool:
                # Honest empty, not a failure -- either every bootstrap
                # alternate for this class is already live/retired, or
                # (reputation-community) there never was a URL-based
                # pool to begin with. Same "completes as not_configured"
                # philosophy as _complete_unconfigured_scan.
                agent_run = vault.create_agent_run_idempotent(
                    task_id=task_id,
                    db=db,
                    agent_name=_agent_name("source-discovery-lifecycle", envelope),
                    campaign_id=campaign_id,
                    function_id=FUNCTION_ID_128,
                    status="succeeded",
                    input_payload={"signal_class": signal_class, "candidate_count": 0},
                    output_payload={"status": "no_candidates"},
                )
                db.set_result_ref(
                    task_id,
                    {
                        "status": "no_candidates",
                        "signal_class": signal_class,
                        "campaign_id": campaign_id,
                        "agent_run_id": agent_run["id"],
                    },
                )
                db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
                db.advance_dependents(task_id)
                return

            probe_batch = vault.create_signal(
                source=f"function-{FUNCTION_ID_128}",
                signal_type=PROBE_BATCH_TYPE,
                payload={"signal_class": signal_class, "candidates": candidate_pool},
                campaign_id=campaign_id,
                function_id=FUNCTION_ID_128,
            )
            # Every candidate's evidence_ref now points at this real,
            # just-created signal row -- resolvable, not free text
            # (prompt.md hard rule 1) -- overwriting the bootstrap://
            # placeholder _bootstrap_candidate_pool set above.
            for item in candidate_pool:
                item["probe"]["evidence_ref"] = f"vault://signal/{probe_batch['id']}"

            agent_run = vault.create_agent_run_idempotent(
                task_id=task_id,
                db=db,
                agent_name=_agent_name("source-discovery-lifecycle", envelope),
                campaign_id=campaign_id,
                function_id=FUNCTION_ID_128,
                status="running",
                input_payload={
                    "signal_class": signal_class,
                    "candidate_count": len(candidate_pool),
                },
            )

            payload = {
                "signal_class": signal_class,
                "card_kind": "source.promote",
                "candidate_pool": candidate_pool,
                "known_urls": sorted(exclude_urls),
            }
            _validate_function_input(FUNCTION_ID_128, payload)

            with clients.build_gateway_client() as gateway:
                with emit_task_span(
                    task_type,
                    function_id=FUNCTION_ID_128,
                    task_ref=task_id,
                    model="claude-haiku",
                    run_id=str(envelope.campaign_id),
                ) as span:
                    response, cost = _complete_and_meter(
                        gateway,
                        vault,
                        model="claude-haiku",
                        system_prompt=_read_prompt(FUNCTION_ID_128),
                        user_content=json.dumps(payload),
                        agent_run_id=agent_run["id"],
                    )
                    set_span_attribute(span, "cost", cost)

            output = _parse_json_content(response["content"])
            core._validate_function_output(FUNCTION_ID_128, output)

            if len(output["candidates"]) < 2:
                # contracts/option-card.schema.json requires >=2 options;
                # Fn 128's own output.schema.json allows 1 (an honestly
                # thin pool). Dead-letter rather than crash on build_card
                # -- same shape as compose_options_handler's own
                # <2-survived path.
                vault.update_agent_run(
                    agent_run["id"],
                    status="failed",
                    output_payload=output,
                    completed_at=_now_iso(),
                )
                db.set_result_ref(
                    task_id,
                    {
                        "status": "insufficient_candidates",
                        "signal_class": signal_class,
                        "candidate_count": len(output["candidates"]),
                        "campaign_id": campaign_id,
                    },
                )
                db.transition(task_id, TaskStateEnum.FAILED, TransitionReason.QA_BLOCKED)
                return

            pool_by_url = {item["url"]: item for item in candidate_pool}
            options = _source_lifecycle_options(
                output["candidates"], pool_by_url, task_type=task_type
            )
            option_ids = {o["option_id"] for o in options}
            recommended = output["recommended_option_id"]
            if recommended not in option_ids:
                recommended = options[0]["option_id"]

            card = build_card(
                kind="source.promote",
                # Matches config.source_promotion's own precedent
                # (services/gatekeeper/policy/autonomy.yaml): a scan-
                # profile/allow-list change is a security-relevant
                # configuration change, never above level 1 however
                # strong the probe evidence looks.
                level=1,
                title=f"New source for {signal_class}"[:120],
                decision_question="Which candidate source should be added to this signal class?",
                options=options,
                recommended=recommended,
                evidence_refs=[
                    {
                        "source_type": "web_source",
                        "ref": f"vault://signal/{probe_batch['id']}",
                        "authority": "secondary",
                    }
                ],
                produced_by={"function_id": 128, "prompt_version": "0.2.0"},
                register_rows=["H31"],
                rationale=output.get(
                    "rationale",
                    f"Bootstrap-derived candidates for {signal_class}, re-scored from "
                    "functions/_shared/source-candidates.bootstrap.yaml.",
                ),
                lineage={"agent_run_id": agent_run["id"], "source_task_id": task_id},
            )

            created = vault.create_option_card(
                {
                    "card_id": card["card_id"],
                    "kind": card["kind"],
                    "autonomy_level": card["autonomy_level"],
                    "risk_tier": card["risk_tier"],
                    "agent_run_id": agent_run["id"],
                    "produced_by_function": 128,
                    "card": card,
                    "created_at": card["created_at"],
                    "expires_at": card["expires_at"],
                }
            )

            vault.update_agent_run(
                agent_run["id"], status="succeeded", output_payload=output, completed_at=_now_iso()
            )

        db.set_result_ref(
            task_id,
            {
                "status": "proposed",
                "card_id": created["card_id"],
                "signal_class": signal_class,
                "candidate_count": len(candidate_pool),
                "agent_run_id": agent_run["id"],
                "campaign_id": campaign_id,
            },
        )
        db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
        db.advance_dependents(task_id)

    handler.__name__ = f"{task_type.replace('-', '_')}_handler"
    return handler


SOURCE_DISCOVERY_HANDLERS = {
    task_type: _make_source_discovery_handler(task_type, signal_class)
    for task_type, signal_class in SOURCE_DISCOVERY_TASKS.items()
}


def source_yield_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    """Fn 128 nightly yield pass (prompt.md task step 5), SCOPE-CUT
    version: writes one yield row per LIVE source (all twelve scan-
    profiles.yaml profiles' `urls`) recording reachability via mcp-web's
    existing `probe_url` tool. The full funnel this step describes
    (signals produced -> cards produced -> cards chosen; cost per chosen
    card) needs every one of the eleven scanners tagging its own signals
    by source URL, which none do today (ingest_signals_handler's output
    carries no per-source attribution) -- instrumenting that is its own,
    separate change, out of scope here and documented rather than
    silently pretended-done. This writes what the vault CAN measure
    honestly today: is each configured source still reachable, so a
    persistently-unreachable source is visible before the monthly retire
    pass below has to act on it."""
    document = scan_shared._load_scan_profiles()
    with clients.build_vault_client() as vault, clients.build_mcp_web_client() as mcp:
        campaign_id = vault.get_or_create_campaign(
            _campaign_name(envelope), function_id=FUNCTION_ID_128
        )
        rows: list[dict[str, Any]] = []
        for profile in document.get("profiles", []):
            for url in profile.get("urls") or []:
                try:
                    probe = mcp.call_tool("probe_url", {"url": url})
                    reachable = int(probe.get("status_code") or 0) == 200
                except Exception as exc:  # noqa: BLE001 - one bad source is a RESULT, not a crash
                    reachable = False
                    log_event(
                        logger,
                        logging.WARNING,
                        "source_yield_probe_failed",
                        url=url,
                        error=sanitize_exception_text(exc),
                    )
                rows.append(
                    {
                        "profile_id": profile["profile_id"],
                        "url": url,
                        "reachable": reachable,
                        "checked_at": _now_iso(),
                    }
                )
        signal = vault.create_signal(
            source=f"function-{FUNCTION_ID_128}",
            signal_type=YIELD_SIGNAL_TYPE,
            payload={"rows": rows},
            campaign_id=campaign_id,
            function_id=FUNCTION_ID_128,
        )
    db.set_result_ref(
        task_id,
        {
            "status": "yield_recorded",
            "vault_signal_id": signal["id"],
            "source_count": len(rows),
            "unreachable_count": sum(1 for row in rows if not row["reachable"]),
            "campaign_id": campaign_id,
        },
    )
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)


def source_retire_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    """Fn 128 monthly retire pass (prompt.md task step 6-7). Self-gating:
    runs inside the daily loop like every other source-lifecycle task
    (report_month_end_handler has the same unresolved "no external
    monthly trigger exists in this repo" gap -- see that handler's own
    history), but no-ops as `not_due` unless RETIRE_PASS_MIN_DAYS have
    passed since its own last real pass, tracked via its own marker
    signal rather than needing infrastructure this repo does not have.

    A source is retire-eligible for either of two reasons, per hard rule
    5 always alongside a replacement candidate on the same card, never a
    bare drop:
      * yield floor breach -- its last YIELD_FLOOR_CONSECUTIVE_FAILURES
        nightly checks (source_yield_handler above) were ALL unreachable;
      * provisional expiry -- a Stage 0 hand-seeded source whose own
        review_by date has passed without re-ratification (still
        `provisional` in the live scan-profiles.yaml today)."""
    now = datetime.now(timezone.utc)
    with clients.build_vault_client() as vault:
        campaign_id = vault.get_or_create_campaign(
            _campaign_name(envelope), function_id=FUNCTION_ID_128
        )
        recent = vault.list_signals(limit=LIFECYCLE_SIGNAL_LOOKBACK)

        last_pass_at: datetime | None = None
        for row in recent:
            if row.get("signal_type") != RETIRE_PASS_MARKER_TYPE:
                continue
            candidate_time = _parse_iso_timestamp(row.get("received_at"))
            if candidate_time and (last_pass_at is None or candidate_time > last_pass_at):
                last_pass_at = candidate_time
        if last_pass_at and (now - last_pass_at).days < RETIRE_PASS_MIN_DAYS:
            db.set_result_ref(
                task_id,
                {
                    "status": "not_due",
                    "next_due_in_days": RETIRE_PASS_MIN_DAYS - (now - last_pass_at).days,
                    "campaign_id": campaign_id,
                },
            )
            db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
            db.advance_dependents(task_id)
            return

        yield_history_by_url: dict[str, list[bool]] = {}
        for row in recent:
            if row.get("signal_type") != YIELD_SIGNAL_TYPE:
                continue
            for entry in (row.get("payload") or {}).get("rows", []):
                yield_history_by_url.setdefault(str(entry.get("url")), []).append(
                    bool(entry.get("reachable"))
                )

        document = scan_shared._load_scan_profiles()
        expired_provisional = _expired_provisional_urls(document, as_of=now.date())
        exclude_urls = _live_source_urls() | _retired_source_urls(vault)

        retired_this_pass: list[dict[str, Any]] = []
        for profile in document.get("profiles", []):
            for url in profile.get("urls") or []:
                history = yield_history_by_url.get(url, [])
                floor_breached = (
                    len(history) >= YIELD_FLOOR_CONSECUTIVE_FAILURES
                    and not any(history[-YIELD_FLOOR_CONSECUTIVE_FAILURES:])
                )
                provisional_expired = url in expired_provisional
                if not (floor_breached or provisional_expired):
                    continue

                signal_class = SCAN_PROFILE_SIGNAL_CLASS.get(profile["profile_id"])
                if signal_class is None:
                    continue
                replacement_pool = _bootstrap_candidate_pool(
                    signal_class, exclude_urls=exclude_urls | {url}, as_of=now.date()
                )
                if len(replacement_pool) < 1:
                    # Hard rule 5: a retirement always carries a
                    # replacement. No replacement -> skip, never emit a
                    # bare drop; this source stays flagged and is
                    # revisited next pass.
                    continue

                agent_run = vault.create_agent_run_idempotent(
                    task_id=task_id,
                    db=db,
                    agent_name=_agent_name("source-discovery-lifecycle", envelope),
                    campaign_id=campaign_id,
                    function_id=FUNCTION_ID_128,
                    status="running",
                    input_payload={"retiring_source_url": url, "signal_class": signal_class},
                )
                probe_batch = vault.create_signal(
                    source=f"function-{FUNCTION_ID_128}",
                    signal_type=PROBE_BATCH_TYPE,
                    payload={
                        "signal_class": signal_class,
                        "candidates": replacement_pool,
                        "retiring_source_url": url,
                    },
                    campaign_id=campaign_id,
                    function_id=FUNCTION_ID_128,
                )
                for item in replacement_pool:
                    item["probe"]["evidence_ref"] = f"vault://signal/{probe_batch['id']}"

                payload = {
                    "signal_class": signal_class,
                    "card_kind": "source.retire",
                    "candidate_pool": replacement_pool,
                    "known_urls": sorted(exclude_urls),
                    "retiring_source_url": url,
                }
                _validate_function_input(FUNCTION_ID_128, payload)

                with clients.build_gateway_client() as gateway:
                    with emit_task_span(
                        "source-retire-monthly",
                        function_id=FUNCTION_ID_128,
                        task_ref=task_id,
                        model="claude-haiku",
                        run_id=str(envelope.campaign_id),
                    ) as span:
                        response, cost = _complete_and_meter(
                            gateway,
                            vault,
                            model="claude-haiku",
                            system_prompt=_read_prompt(FUNCTION_ID_128),
                            user_content=json.dumps(payload),
                            agent_run_id=agent_run["id"],
                        )
                        set_span_attribute(span, "cost", cost)

                output = _parse_json_content(response["content"])
                core._validate_function_output(FUNCTION_ID_128, output)
                if len(output["candidates"]) < 2:
                    vault.update_agent_run(
                        agent_run["id"],
                        status="failed",
                        output_payload=output,
                        completed_at=_now_iso(),
                    )
                    continue

                pool_by_url = {item["url"]: item for item in replacement_pool}
                options = _source_lifecycle_options(
                    output["candidates"], pool_by_url, task_type="source-retire-monthly"
                )
                option_ids = {o["option_id"] for o in options}
                recommended = output["recommended_option_id"]
                if recommended not in option_ids:
                    recommended = options[0]["option_id"]

                reason = (
                    f"{url} returned no reachable result across its last "
                    f"{YIELD_FLOOR_CONSECUTIVE_FAILURES} nightly yield checks."
                    if floor_breached
                    else f"{url} is a Stage 0 provisional source whose review_by date has "
                    "passed without re-ratification."
                )
                card = build_card(
                    kind="source.retire",
                    level=1,
                    title=f"Retire {url}"[:120],
                    decision_question=(
                        "Retire this underperforming source and replace it with which candidate?"
                    ),
                    options=options,
                    recommended=recommended,
                    evidence_refs=[
                        {
                            "source_type": "web_source",
                            "ref": f"vault://signal/{probe_batch['id']}",
                            "authority": "secondary",
                        }
                    ],
                    produced_by={"function_id": 128, "prompt_version": "0.2.0"},
                    register_rows=["H31"],
                    rationale=output.get("rationale", reason),
                    lineage={"agent_run_id": agent_run["id"], "source_task_id": task_id},
                )
                created = vault.create_option_card(
                    {
                        "card_id": card["card_id"],
                        "kind": card["kind"],
                        "autonomy_level": card["autonomy_level"],
                        "risk_tier": card["risk_tier"],
                        "agent_run_id": agent_run["id"],
                        "produced_by_function": 128,
                        "card": card,
                        "created_at": card["created_at"],
                        "expires_at": card["expires_at"],
                    }
                )
                vault.update_agent_run(
                    agent_run["id"],
                    status="succeeded",
                    output_payload=output,
                    completed_at=_now_iso(),
                )
                vault.create_signal(
                    source=f"function-{FUNCTION_ID_128}",
                    signal_type=RETIRED_SOURCE_SIGNAL_TYPE,
                    payload={
                        "url": url,
                        "profile_id": profile["profile_id"],
                        "retire_card_id": created["card_id"],
                        "reason": "yield_floor" if floor_breached else "provisional_expired",
                    },
                    campaign_id=campaign_id,
                    function_id=FUNCTION_ID_128,
                )
                retired_this_pass.append({"url": url, "card_id": created["card_id"]})
                exclude_urls.add(url)

        vault.create_signal(
            source=f"function-{FUNCTION_ID_128}",
            signal_type=RETIRE_PASS_MARKER_TYPE,
            payload={"retired_count": len(retired_this_pass)},
            campaign_id=campaign_id,
            function_id=FUNCTION_ID_128,
        )

    db.set_result_ref(
        task_id,
        {
            "status": "retire_pass_complete",
            "retired_count": len(retired_this_pass),
            "retired": retired_this_pass,
            "campaign_id": campaign_id,
        },
    )
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)


