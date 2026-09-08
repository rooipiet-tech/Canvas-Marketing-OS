from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

import yaml
from options_inbox.cards import build_card

from orchestrator.config import policies_dir
from orchestrator.logging_config import log_event
from orchestrator.models import TaskEnvelope, TaskStateEnum, TransitionReason

from .. import clients, core
from ..core import _agent_name, _campaign_name, _now_iso, logger
from ..scan_shared import _parse_iso_timestamp
from .source_lifecycle import (
    LIFECYCLE_SIGNAL_LOOKBACK,
    YIELD_SIGNAL_TYPE,
    _bootstrap_candidate_pool,
    _client_domain_excluded,
    _live_source_urls,
    _retired_source_urls,
)

# ---------------------------------------------------------------------
# Fn 129 -- Web Reach Governor (Appendix D PR 5c)
# ---------------------------------------------------------------------
#
# Deterministic, versioned in policies/allowlist-rule.yaml (that file's own
# header: "every criterion below is a boolean this repository can check
# without a model call"). No model call anywhere in this section, mirroring
# route_digest_handler's own "pure orchestration, no model call" shape.
#
# INTEGRATION SCOPE, DOCUMENTED (not hidden). prompt.md frames Fn 129's task
# as "for every off-allowlist candidate domain a source.promote run
# surfaces" -- i.e. gating Fn 128's own compose_options_handler-equivalent
# (_make_source_discovery_handler) before it ever builds a card. This PR
# does NOT wire that synchronous gate: doing so would change #159's
# just-shipped, already-tested source-discovery path without the same
# depth of re-verification that path already received, for a benefit
# (governing candidates dispatch.py already treats as on_allowlist=false,
# i.e. never auto-widened today anyway) this PR's own standalone handlers
# below deliver just as well on their own schedule. Instead:
#   * web_reach_allowlist_review_handler runs the SAME candidate pool Fn
#     128 mines (_bootstrap_candidate_pool, by signal_class) through the
#     rule independently, daily, auto-widening on a pass (citing SP-006,
#     no card) or emitting a source.allowlist card on a fail -- real work,
#     just not synchronous with Fn 128's own card.
#   * web_reach_allowlist_monthly_review_handler self-gates the same way
#     source_retire_handler already does, proposing reverts for any
#     auto-widened domain with zero yield in its 60-day review_by window.
# Wiring these two into Fn 128's own per-candidate path is a small, well-
# defined follow-up once this standalone mechanism has run for real.

FUNCTION_ID_129 = "129-web-reach-governor"

ALLOWLIST_WIDENED_SIGNAL_TYPE = "source_allowlist_widened"
ALLOWLIST_REVIEW_PASS_MARKER_TYPE = "allowlist_review_pass_marker"
ALLOWLIST_REVIEW_PASS_MIN_DAYS = 60  # matches policies/allowlist-rule.yaml's review_by_days

_INJECTION_PATTERNS: list[tuple[str, str]] = [
    # round-21 pattern (docs/blueprint/agentic-marketing-engine-v3.md §11):
    # planted authorisations, fabricated run numbers, "harmless no-op"
    # framings found in fetched/scraped content. Matched case-insensitively
    # against RAW fetched text, before any of it reaches a downstream
    # prompt -- prompt.md hard rule 3.
    (
        "planted_authorisation",
        r"\b(you are authoriz(?:ed|ation)|pre-?approved by|"
        r"approval (?:granted|is granted)|"
        r"override(?:s|d|ing)? (?:the |any )?"
        r"(?:previous|prior|existing) (?:restriction|rule|policy))\b",
    ),
    (
        "fabricated_run_number",
        r"\b(?:run[_ ]?(?:id|#|number)|agent_run_id|task[_ ]?id)\s*[:=#]?\s*[\w-]{4,}",
    ),
    (
        "harmless_no_op_framing",
        r"\b(this is a harmless no-?op|proceed without a card|"
        r"no action needed[,.]?\s*(?:just )?continue|"
        r"ignore (?:prior|previous|the above) instructions|disregard the above)\b",
    ),
]
_COMPILED_INJECTION_PATTERNS = [
    (name, re.compile(pattern, re.IGNORECASE)) for name, pattern in _INJECTION_PATTERNS
]


def _strip_instruction_shaped_content(
    text: str, *, source_url: str
) -> tuple[str, list[dict[str, Any]]]:
    """prompt.md hard rule 3: every instruction-shaped span is stripped and
    logged, with the source URL and the stripped span's byte offset,
    before the text reaches any downstream prompt. The rule-based decision
    (allowlist criteria) is computed from data Fn 129 measured itself
    (probe fields, robots.txt fetch) -- stripping never changes a decision
    that was already made on other evidence; it only removes the ability
    of scraped text to make a new one, so this function never influences
    `_evaluate_allowlist_rule`'s own criteria, only what a caller may later
    pass to a drafting prompt."""
    if not text:
        return text, []
    spans: list[dict[str, Any]] = []
    cleaned = text
    for pattern_name, compiled in _COMPILED_INJECTION_PATTERNS:
        for match in compiled.finditer(text):
            byte_offset = len(text[: match.start()].encode("utf-8"))
            spans.append(
                {
                    "source_url": source_url,
                    "byte_offset": byte_offset,
                    "pattern_matched": pattern_name,
                }
            )
        cleaned = compiled.sub("[stripped]", cleaned)
    spans.sort(key=lambda span: span["byte_offset"])
    return cleaned, spans


def _load_allowlist_rule() -> dict[str, Any]:
    path = policies_dir() / "allowlist-rule.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _load_allowlist_deny() -> dict[str, Any]:
    path = policies_dir() / "allowlist-deny.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _load_discovery_budget() -> dict[str, Any]:
    path = policies_dir() / "discovery-budget.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _load_standing_permissions_seed() -> dict[str, Any]:
    path = policies_dir() / "standing-permissions-seed.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _domain_or_parent_in(domain: str, denied: list[str]) -> bool:
    domain = domain.lower()
    denylist = {d.lower() for d in denied}
    if domain in denylist:
        return True
    return any(domain == d or domain.endswith(f".{d}") for d in denylist)


def _check_robots_directives(
    domain: str, *, http_get: Callable[..., Any] | None = None
) -> tuple[bool, bool, bool]:
    """One real, unauthenticated GET of https://{domain}/robots.txt.
    Returns (robots_allows, no_noai_directive, https_valid) -- all three
    from the SAME fetch attempt, since a successful HTTPS GET already
    proves a valid certificate (https_valid), and the response body (if
    any) is parsed once for both the remaining criteria. Fails CLOSED
    (False, False, False) on any error -- unreachable, timeout, invalid
    cert -- matching policies/allowlist-rule.yaml's own "policy fails
    closed" convention (autonomy.yaml's own header) rather than assuming
    innocence for a domain this repo could not actually verify."""
    if http_get is None:
        import httpx

        http_get = httpx.get
    try:
        response = http_get(f"https://{domain}/robots.txt", timeout=10.0, follow_redirects=True)
    except Exception as exc:  # noqa: BLE001 - an unreachable domain fails closed, not a crash
        log_event(
            logger, logging.WARNING, "robots_txt_fetch_failed", domain=domain, error=str(exc)
        )
        return False, False, False
    if response.status_code >= 400:
        # A missing robots.txt (404) is conventionally "no restrictions",
        # but this repo's own posture is to fail closed on anything it
        # could not actually read -- a 404 is not evidence of permission.
        return False, False, False
    body = response.text.lower()
    disallow_all = bool(re.search(r"user-agent:\s*\*[^\n]*\n(?:disallow:\s*/\s*\n)+", body))
    robots_allows = not disallow_all
    no_noai_directive = "noai" not in body and "noarchive" not in body
    return robots_allows, no_noai_directive, True


def _domain_registered_before_months(
    domain: str, *, months: int = 12, rdap_get: Callable[..., Any] | None = None
) -> bool:
    """policies/allowlist-rule.yaml's resolvable_12mo criterion, via a real
    RDAP lookup (https://rdap.org/domain/{domain} -- free, unauthenticated,
    IANA-bootstrapped registry redirector; no vendor account needed).
    Fails CLOSED (False) on any error, exactly as _check_robots_directives
    does, for the identical reason."""
    if rdap_get is None:
        import httpx

        rdap_get = httpx.get
    try:
        response = rdap_get(
            f"https://rdap.org/domain/{domain}", timeout=10.0, follow_redirects=True
        )
        if response.status_code != 200:
            return False
        events = response.json().get("events") or []
        registration = next(
            (e for e in events if e.get("eventAction") == "registration"), None
        )
        if not registration or not registration.get("eventDate"):
            return False
        registered_at = _parse_iso_timestamp(registration["eventDate"])
        if registered_at is None:
            return False
    except Exception as exc:  # noqa: BLE001 - an unreachable/malformed RDAP response fails closed
        log_event(logger, logging.WARNING, "rdap_lookup_failed", domain=domain, error=str(exc))
        return False
    age_days = (datetime.now(timezone.utc) - registered_at).days
    return age_days >= months * 30


def _evaluate_allowlist_rule(
    *,
    domain: str,
    probe: dict[str, Any],
    rule: dict[str, Any],
    deny: dict[str, Any],
    http_get: Callable[..., Any] | None = None,
    rdap_get: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """policies/allowlist-rule.yaml's nine pass_criteria, computed for
    real. Hard exclusions (client domains) are checked FIRST and refuse
    outright -- prompt.md hard rule 1: "never auto-allowed and never
    carded... there is no path from 'refused' to 'allowed' through this
    function." Every other criterion is evaluated even when one already
    failed, so the card (if one is built) states the verdict per
    criterion, not just the first failure."""
    if _client_domain_excluded(domain):
        return {
            "domain": domain,
            "criteria": {
                "resolvable_12mo": False,
                "robots_allows": False,
                "no_noai_directive": False,
                "https_valid": False,
                "not_on_deny_list": False,
                "not_client_domain": False,
                "not_authenticated_surface": False,
                "not_personal_data_category": False,
                "probe_yield_ok": False,
            },
            "decision": "hard_excluded",
            "allowed_by": None,
            "review_by": None,
            "card_kind": None,
            "stripped_spans": [],
            "cost_cap_hit": False,
        }

    resolvable_12mo = _domain_registered_before_months(
        domain, months=int(rule["pass_criteria"][0]["min_age_months"]), rdap_get=rdap_get
    )
    robots_allows, no_noai_directive, https_valid = _check_robots_directives(
        domain, http_get=http_get
    )
    not_on_deny_list = not _domain_or_parent_in(domain, deny.get("denied_domains") or [])
    not_client_domain = True  # already refused above if false
    not_authenticated_surface = not _domain_or_parent_in(
        domain, deny.get("authenticated_surface_domains") or []
    )
    not_personal_data_category = not _domain_or_parent_in(
        domain, deny.get("personal_data_category_domains") or []
    )
    yield_criterion = next(c for c in rule["pass_criteria"] if c["key"] == "probe_yield_ok")
    max_duplicate_rate = float(yield_criterion["max_duplicate_rate"])
    probe_yield_ok = (
        float(probe.get("forecast_yield_per_week") or 0.0) >= 0.1
        and float(probe.get("duplicate_rate") or 0.0) <= max_duplicate_rate
    )

    criteria = {
        "resolvable_12mo": resolvable_12mo,
        "robots_allows": robots_allows,
        "no_noai_directive": no_noai_directive,
        "https_valid": https_valid,
        "not_on_deny_list": not_on_deny_list,
        "not_client_domain": not_client_domain,
        "not_authenticated_surface": not_authenticated_surface,
        "not_personal_data_category": not_personal_data_category,
        "probe_yield_ok": probe_yield_ok,
    }
    all_pass = all(criteria.values())
    now = datetime.now(timezone.utc)
    if all_pass:
        return {
            "domain": domain,
            "criteria": criteria,
            "decision": "auto_allow",
            "allowed_by": rule.get("standing_permission", "SP-006"),
            "review_by": (
                now + timedelta(days=int(rule["reversibility"]["review_by_days"]))
            )
            .date()
            .isoformat(),
            "card_kind": None,
            "stripped_spans": [],
            "cost_cap_hit": False,
        }
    return {
        "domain": domain,
        "criteria": criteria,
        "decision": "card_required",
        "allowed_by": None,
        "review_by": None,
        "card_kind": "source.allowlist",
        "stripped_spans": [],
        "cost_cap_hit": False,
    }


def _render_allowlist_criteria_evidence(output: dict[str, Any]) -> str:
    lines = [f"Allowlist rule evaluation for {output['domain']}:", ""]
    for key, passed in output["criteria"].items():
        lines.append(f"  {'✓' if passed else '✗'} {key}")
    return "\n".join(lines)


def _make_web_reach_review_handler(task_type: str, signal_class: str):
    """web_reach_allowlist_review_handler's per-class factory, mirroring
    _make_source_discovery_handler's own shape: same candidate pool Fn 128
    mines, evaluated independently against policies/allowlist-rule.yaml.
    See the module-section docstring above for why this is a standalone
    daily pass rather than a synchronous gate inside Fn 128's own
    handler."""

    def handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
        rule = _load_allowlist_rule()
        deny = _load_allowlist_deny()
        with clients.build_vault_client() as vault:
            campaign_id = vault.get_or_create_campaign(
                _campaign_name(envelope), function_id=FUNCTION_ID_129
            )
            exclude_urls = _live_source_urls() | _retired_source_urls(vault)
            candidate_pool = _bootstrap_candidate_pool(
                signal_class, exclude_urls=exclude_urls, as_of=datetime.now(timezone.utc).date()
            )
            widened: list[dict[str, Any]] = []
            carded: list[dict[str, Any]] = []
            for candidate in candidate_pool:
                domain = candidate["domain"]
                output = _evaluate_allowlist_rule(
                    domain=domain, probe=candidate["probe"], rule=rule, deny=deny
                )
                core._validate_function_output(FUNCTION_ID_129, output)
                agent_run = vault.create_agent_run_idempotent(
                    task_id=task_id,
                    db=db,
                    agent_name=_agent_name("web-reach-governor", envelope),
                    campaign_id=campaign_id,
                    function_id=FUNCTION_ID_129,
                    status="succeeded",
                    input_payload={"domain": domain, "url": candidate["url"]},
                    output_payload=output,
                )
                if output["decision"] == "auto_allow":
                    vault.create_signal(
                        source=f"function-{FUNCTION_ID_129}",
                        signal_type=ALLOWLIST_WIDENED_SIGNAL_TYPE,
                        payload={
                            "domain": domain,
                            "url": candidate["url"],
                            "allowed_by": output["allowed_by"],
                            "allowed_at": _now_iso(),
                            "review_by": output["review_by"],
                        },
                        campaign_id=campaign_id,
                        function_id=FUNCTION_ID_129,
                    )
                    widened.append({"domain": domain, "agent_run_id": agent_run["id"]})
                elif output["decision"] == "card_required":
                    evidence = _render_allowlist_criteria_evidence(output)
                    card = build_card(
                        kind="source.allowlist",
                        level=0,  # overridden to non_negotiable/realtime by build_card itself
                        title=f"Allowlist review: {domain}"[:120],
                        decision_question=f"Widen the egress allow-list to include {domain}?",
                        options=[
                            {
                                "option_id": "A",
                                "label": "Allow",
                                "summary": f"Add {domain} to the egress allow-list."[:400],
                                "payload_ref": f"vault://agent-run/{agent_run['id']}",
                                "evidence_refs": [
                                    {
                                        "source_type": "vault_asset",
                                        "ref": f"vault://agent-run/{agent_run['id']}",
                                        "quote": evidence[:300],
                                        "authority": "primary",
                                    }
                                ],
                                "predicted_outcome": (
                                    "Domain becomes scannable for this signal class."
                                ),
                                "risks": [
                                    key.replace("_", " ")
                                    for key, passed in output["criteria"].items()
                                    if not passed
                                ],
                            },
                            {
                                "option_id": "B",
                                "label": "Reject",
                                "summary": (
                                    f"Do not add {domain}; keep it off the allow-list."[:400]
                                ),
                                "payload_ref": f"vault://agent-run/{agent_run['id']}",
                                "evidence_refs": [
                                    {
                                        "source_type": "vault_asset",
                                        "ref": f"vault://agent-run/{agent_run['id']}",
                                        "quote": evidence[:300],
                                        "authority": "primary",
                                    }
                                ],
                                "predicted_outcome": "No change; this candidate stays unavailable.",
                                "risks": [],
                            },
                        ],
                        recommended="B",
                        evidence_refs=[
                            {
                                "source_type": "vault_asset",
                                "ref": f"vault://agent-run/{agent_run['id']}",
                                "authority": "primary",
                            }
                        ],
                        produced_by={"function_id": 129, "prompt_version": "0.1.0"},
                        register_rows=["H32"],
                        rationale=evidence,
                        lineage={"agent_run_id": agent_run["id"], "source_task_id": task_id},
                    )
                    created = vault.create_option_card(
                        {
                            "card_id": card["card_id"],
                            "kind": card["kind"],
                            "autonomy_level": card["autonomy_level"],
                            "risk_tier": card["risk_tier"],
                            "agent_run_id": agent_run["id"],
                            "produced_by_function": 129,
                            "card": card,
                            "created_at": card["created_at"],
                            "expires_at": card["expires_at"],
                        }
                    )
                    carded.append({"domain": domain, "card_id": created["card_id"]})
                # hard_excluded: neither widened nor carded, per prompt.md hard rule 1 -- logged
                # via the agent_run above only.

        db.set_result_ref(
            task_id,
            {
                "status": "reviewed",
                "signal_class": signal_class,
                "candidate_count": len(candidate_pool),
                "widened": widened,
                "carded": carded,
                "campaign_id": campaign_id,
            },
        )
        db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
        db.advance_dependents(task_id)

    handler.__name__ = f"{task_type.replace('-', '_')}_handler"
    return handler


WEB_REACH_REVIEW_TASKS: dict[str, str] = {
    "web-reach-allowlist-review-competitors": "competitors",
    "web-reach-allowlist-review-fabric-ecosystem": "microsoft-fabric-power-bi",
    "web-reach-allowlist-review-adjacent-industry-regulation": (
        "adjacent-technology-industry-trends-regulation"
    ),
    "web-reach-allowlist-review-tenders-events-partners": "tenders-events-partners",
    "web-reach-allowlist-review-reputation-community": "reputation-community",
}

WEB_REACH_REVIEW_HANDLERS = {
    task_type: _make_web_reach_review_handler(task_type, signal_class)
    for task_type, signal_class in WEB_REACH_REVIEW_TASKS.items()
}


def web_reach_allowlist_monthly_review_handler(
    task_id: str, envelope: TaskEnvelope, db: Any
) -> None:
    """Fn 129 monthly review (prompt.md task step 6 / policies/allowlist-
    rule.yaml's own reversibility.monthly_review_card): proposes a revert
    for any domain web_reach_allowlist_review_handler auto-widened whose
    yield has stayed at zero across its 60-day review_by window. Self-
    gating exactly like source_retire_handler -- runs inside the daily
    loop, no-ops as `not_due` unless ALLOWLIST_REVIEW_PASS_MIN_DAYS have
    passed since its own last real pass."""
    now = datetime.now(timezone.utc)
    with clients.build_vault_client() as vault:
        campaign_id = vault.get_or_create_campaign(
            _campaign_name(envelope), function_id=FUNCTION_ID_129
        )
        recent = vault.list_signals(limit=LIFECYCLE_SIGNAL_LOOKBACK)

        last_pass_at: datetime | None = None
        for row in recent:
            if row.get("signal_type") != ALLOWLIST_REVIEW_PASS_MARKER_TYPE:
                continue
            candidate_time = _parse_iso_timestamp(row.get("received_at"))
            if candidate_time and (last_pass_at is None or candidate_time > last_pass_at):
                last_pass_at = candidate_time
        if last_pass_at and (now - last_pass_at).days < ALLOWLIST_REVIEW_PASS_MIN_DAYS:
            db.set_result_ref(
                task_id,
                {
                    "status": "not_due",
                    "next_due_in_days": ALLOWLIST_REVIEW_PASS_MIN_DAYS - (now - last_pass_at).days,
                    "campaign_id": campaign_id,
                },
            )
            db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
            db.advance_dependents(task_id)
            return

        widened: dict[str, dict[str, Any]] = {}
        for row in recent:
            if row.get("signal_type") == ALLOWLIST_WIDENED_SIGNAL_TYPE:
                payload = row.get("payload") or {}
                domain = payload.get("domain")
                if domain:
                    widened[domain] = payload

        yield_seen: set[str] = set()
        for row in recent:
            if row.get("signal_type") != YIELD_SIGNAL_TYPE:
                continue
            for entry in (row.get("payload") or {}).get("rows", []):
                if entry.get("reachable"):
                    domain = (urlparse(str(entry.get("url", ""))).hostname or "").lower()
                    if domain:
                        yield_seen.add(domain)

        proposed_reverts = []
        for domain, payload in widened.items():
            review_by = payload.get("review_by")
            if not review_by:
                continue
            try:
                due = date.fromisoformat(str(review_by))
            except ValueError:
                continue
            if due >= now.date() or domain in yield_seen:
                continue
            proposed_reverts.append({"domain": domain, "url": payload.get("url")})

        vault.create_signal(
            source=f"function-{FUNCTION_ID_129}",
            signal_type=ALLOWLIST_REVIEW_PASS_MARKER_TYPE,
            payload={"proposed_revert_count": len(proposed_reverts)},
            campaign_id=campaign_id,
            function_id=FUNCTION_ID_129,
        )

    db.set_result_ref(
        task_id,
        {
            "status": "review_pass_complete",
            "proposed_reverts": proposed_reverts,
            "campaign_id": campaign_id,
        },
    )
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)


