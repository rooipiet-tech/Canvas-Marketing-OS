from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

import yaml

from orchestrator.clients.vault_client_ext import VaultClientExt
from orchestrator.config import functions_dir
from orchestrator.dispatch_errors import DispatchError
from orchestrator.logging_config import log_event, sanitize_exception_text
from orchestrator.models import TaskEnvelope

from .core import logger

DEFAULT_MIN_INGEST_SOURCES = 2
DEFAULT_MIN_INGEST_DOMAINS = 2

# F-INGEST-CONTENT-FLOOR. The floors above count URLs and hostnames and
# never once look at what came back in them, which is how a stale
# ca-mcp-web serving a 176-byte synthetic fixture for EVERY fetch_url
# passed every guard in this file for three weeks (10 Aug - 2 Sep 2026).
# Four URLs across three hosts is a healthy-looking scan by both floors;
# `evidence_chars: 704` is 176 x 4, byte-exact, and the model was asked
# for three attributed signals over what amounted to four copies of a
# stub. It emitted none, ingest-signals dead-lettered, and the loop
# cascaded -- while the smoke test that would have named the cause was
# being evicted by the concurrency race.
#
# 500 is chosen to sit well clear of that 176 while staying far below
# any real feed or article: the smallest realistic shaped bodies in this
# repo's own fixtures are a couple of hundred characters of deliberately
# truncated test XML, and a live Moneyweb feed or learn.microsoft.com
# page shapes to thousands. Per-profile override exists for the same
# reason every other floor has one -- a genuinely terse source is a
# reviewed YAML line, not a code change.
DEFAULT_MIN_INGEST_SOURCE_CHARS = 500

# F-INGEST-QUIET-SCAN. What an ordinary morning is expected to produce --
# NOT a floor. schema.json enforces minItems 1; this is the count below
# which a scan is worth a WARNING line, so a run of quiet days is visible
# without any of them failing.
#
# It is 3 because that is what prompt.md hard rule 1 asks for, and the
# two must not drift apart again: a prompt asking for three while a
# schema demanded three, next to a rule 9 forbidding padding to reach
# three, is the contradiction this replaces.
def _ingest_floors(sources: dict[str, Any]) -> tuple[int, int]:
    """Minimum surviving sources and distinct domains for a scan to count.

    Read from scan-profiles.yaml rather than hardcoded here, mirroring
    routing.yaml/budgets.yaml's policy-as-data convention -- relaxing a
    floor (or raising it once a profile has more sources) is one reviewed
    YAML line, not a code change and redeploy.
    """
    return (
        int(sources.get("min_sources", DEFAULT_MIN_INGEST_SOURCES)),
        int(sources.get("min_distinct_domains", DEFAULT_MIN_INGEST_DOMAINS)),
    )


def _ingest_min_source_chars(sources: dict[str, Any]) -> int:
    """Shaped-body length below which a source is not evidence.

    Config-driven for the same reason _ingest_floors is. Note this is a
    floor on the SHAPED body (feed items, de-marked-up page text), not on
    the raw response -- 8 KB of RSS <channel> preamble is not evidence
    either, which F-INGEST-EVIDENCE-WINDOW already established.
    """
    return int(sources.get("min_source_chars", DEFAULT_MIN_INGEST_SOURCE_CHARS))


def _ingest_source_chars(sources: dict[str, Any]) -> int:
    """Per-source evidence budget, read from scan-profiles.yaml for the
    same reason the floors are (see _ingest_floors)."""
    return int(sources.get("source_chars", DEFAULT_INGEST_SOURCE_CHARS))


def _distinct_domains(urls: list[str]) -> set[str]:
    """Hostnames, lowercased. Two feeds on one host are one domain -- the
    same reading prompt.md's domain-diversity rule uses ("three headlines
    from one vendor blog is one signal, not three"), and the reason the
    floor is checked on domains and not only on source count:
    the market-intelligence profile's 4 URLs span only 3 hosts."""
    return {(urlparse(url).hostname or "").lower() for url in urls if url}


def _substantive_sources(
    fetched: list[dict[str, str]], min_source_chars: int
) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    """Split fetched sources into those carrying evidence and those not.

    A source whose shaped body is shorter than `min_source_chars` did not
    fail -- fetch_url returned 200 and a body -- it simply returned
    nothing worth reasoning over. Counting it toward the source and
    domain floors is what let the three-week fixture outage look like a
    complete scan (see DEFAULT_MIN_INGEST_SOURCE_CHARS).

    Returns (substantive, thin); `thin` carries each url with its length
    so the failure and the log line can name the actual numbers rather
    than assert that something was wrong.
    """
    substantive: list[dict[str, str]] = []
    thin: list[dict[str, Any]] = []
    for item in fetched:
        length = len(item.get("body") or "")
        if length >= min_source_chars:
            substantive.append(item)
        else:
            thin.append({"url": item["url"], "body_chars": length})
    return substantive, thin


def _assert_ingest_floor(
    stage: str,
    fetched: list[dict[str, str]],
    min_sources: int,
    min_domains: int,
    min_source_chars: int,
) -> list[dict[str, str]]:
    """Raise DispatchError when the SUBSTANTIVE sources are below a floor.

    Called twice per run against the same floors: once on what fetch_url
    actually returned (before any model spend), and once on what survived
    the redaction fallback's source-dropping (after it, since that loop
    can take a passing set below the floor). `stage` names which, so the
    failure says where the sources were lost.

    Returns the substantive subset so the caller reasons about the same
    set the floor was checked against, rather than re-deriving it.
    """
    substantive, thin = _substantive_sources(fetched, min_source_chars)
    urls = [item["url"] for item in substantive]
    domains = _distinct_domains(urls)

    if thin:
        # Emitted whether or not the floor is met: a source that came back
        # near-empty is worth seeing on a scan that still passed, because
        # that is what the fixture outage looked like on the days it had
        # enough other sources to survive.
        log_event(
            logger,
            logging.WARNING,
            "ingest_source_below_content_floor",
            stage=stage,
            min_source_chars=min_source_chars,
            thin_sources=thin,
        )

    if len(urls) >= min_sources and len(domains) >= min_domains:
        return substantive

    raise DispatchError(
        f"ingest-signals: {stage} left {len(urls)} source(s) across "
        f"{len(domains)} domain(s) carrying at least {min_source_chars} characters "
        f"of evidence, below the floor of {min_sources} source(s) / "
        f"{min_domains} domain(s)"
        + (
            f" ({len(thin)} source(s) returned a body but too little of one: {thin})"
            if thin
            else ""
        )
        + " -- a scan below this floor cannot satisfy function 09's own "
        "at-least-2-distinct-domains rule, so it is failed rather than written "
        "to the Vault as if it were a complete scan"
    )


def _assert_signal_domain_floor(
    output: dict[str, Any], min_domains: int, available_domains: int | None = None
) -> None:
    """Enforce prompt.md hard rule 3 -- the one contract rule schema.json
    structurally cannot express, since JSON Schema cannot say "the set of
    hostnames across this array has at least N members". Without this, a
    batch citing one domain three times passes validation and reaches the
    Vault looking like three corroborated signals.

    The floor is capped at what retrieval ACTUALLY delivered
    (`available_domains`). A model cannot cite two domains when only one
    resolved, so on a degraded day the uncapped floor would fail a batch
    for a shortfall it had no way to avoid -- punishing the scan for the
    fetch layer's bad morning. Capping keeps rule 3 fully enforced
    whenever it is satisfiable, which is the only time enforcing it says
    anything, and is what makes relaxing the RETRIEVAL floor a decision
    about how many sources a scan needs rather than a quiet weakening of
    what the model is held to."""
    effective = min_domains if available_domains is None else min(min_domains, available_domains)
    cited = [str(item.get("source_url", "")) for item in _batch_items(output)]
    domains = _distinct_domains(cited)
    if len(domains) >= effective:
        return
    # F-INGEST-QUIET-ZERO. Rule 3 constrains how signals may be
    # ATTRIBUTED; it has nothing to say about a batch with no signals to
    # attribute. Without this, relaxing schema.json's minItems to 0 moved
    # the empty-batch failure here instead of fixing it -- the third gate
    # in a row that turned an honest zero into a dead-letter, after the
    # schema and score-signals. Caught by
    # test_an_empty_batch_completes_and_marks_itself_quiet, not by
    # reading.
    #
    # Deliberately not folded into `effective`: capping the floor at 0
    # would also excuse a ONE-signal batch from citing a domain, and rule
    # 3 must keep biting the moment there is anything to cite.
    if not cited:
        return
    raise DispatchError(
        f"ingest-signals: emitted signals cite {len(domains)} distinct domain(s), "
        f"below the effective floor of {effective} (configured {min_domains}, "
        f"{available_domains} retrieved) -- prompt.md hard rule 3 "
        "(schema.json cannot express a cross-item uniqueness constraint)"
    )


SCAN_PROFILES_PATH = ("_shared", "scan-profiles.yaml")
DEFAULT_SCAN_PROFILE_ID = "market-intelligence"


def _load_scan_profiles() -> dict[str, Any]:
    """functions/_shared/scan-profiles.yaml, resolved through
    functions_dir() at call time (see config.functions_dir()'s docstring
    for why nothing here resolves a path at module import).

    Replaced functions/09-market-intelligence-director/fetch_sources.yaml
    (F-SCAN-PROFILE-SINGLETON): that file described ONE scan -- `topic`
    and `horizon_days` were scalars at the root -- while eleven further
    scanner packages sat complete-but-unwired with nowhere to say what
    each of them scans. It also lived inside function 09's package while
    describing work for twelve functions."""
    path = functions_dir().joinpath(*SCAN_PROFILES_PATH)
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _resolve_scan_profile(profile_id: str, *, require_urls: bool = True) -> dict[str, Any]:
    """One profile, with `defaults` merged underneath its own keys.

    Refuses, rather than degrades, in both failure cases:

      * an unknown profile_id -- a typo in a loop YAML's params must not
        silently fall back to scanning the wrong market;
      * a profile with no `urls` -- a scan of nothing is not a scan. The
        error names the file and the profile so the fix is obvious from
        the failure alone.

    `require_urls=False` is for the eleven fan-out scanners, which are
    deliberately sourceless today (see the profiles file's own header).
    Those complete as not-configured rather than failing, so eleven
    unfilled profiles do not make a red daily loop the normal state --
    see _make_scanner_handler.
    """
    document = _load_scan_profiles()
    profiles = {entry["profile_id"]: entry for entry in document.get("profiles", [])}
    profile = profiles.get(profile_id)
    if profile is None:
        raise DispatchError(
            f"scan profile {profile_id!r} is not defined in "
            f"functions/{'/'.join(SCAN_PROFILES_PATH)} "
            f"(defined: {', '.join(sorted(profiles))})"
        )
    resolved = {**document.get("defaults", {}), **profile}
    if require_urls and not resolved.get("urls"):
        raise DispatchError(
            f"scan profile {profile_id!r} has no source urls in "
            f"functions/{'/'.join(SCAN_PROFILES_PATH)} -- this scanner cannot run "
            "until its sources are filled in; it is refused rather than scanned empty"
        )
    return resolved


def _envelope_scan_profile_id(envelope: TaskEnvelope) -> str:
    """The loop task's own `params.profile_id`, or the market-intelligence
    default. Metadata values arrive as strings (worker._task_metadata)."""
    if envelope.metadata:
        return str(envelope.metadata.get("profile_id") or DEFAULT_SCAN_PROFILE_ID)
    return DEFAULT_SCAN_PROFILE_ID


DEFAULT_INGEST_SOURCE_CHARS = 8000

# max_tokens for the ingest completion. The gateway client's 1536 default
# is a tight ceiling for up to 8 signals of headline + so_what + URL plus a
# summary paragraph, and a truncated completion fails as invalid JSON (the
# F-WEDNESDAY-DRAFT-TRUNCATION failure mode -- see _complete_and_meter's
# docstring). Raised alongside the input budget so both ends of the call
# have room.

# F-INGEST-NO-MEMORY (this change). Every scan started cold. The
# market-intelligence profile runs DAILY against a THIRTY-day horizon, so
# the same story stayed in-window -- and eligible to be re-reported -- for
# up to thirty consecutive runs. `vault_signal_lookup` was declared in
# function 09's tools.yaml from the start and never implemented anywhere;
# dedupe-signal-cards, the task that would have caught repeats downstream,
# is one of the seventeen no-ops.
#
# The Vault already answers this: GET /signals is in the frozen vault-api
# contract. It takes limit/offset only, no server-side filter, so the
# narrowing to "this profile, inside this horizon" happens here.
#
# The exclusion list is given to the MODEL rather than applied as a hard
# post-filter, on purpose. schema.json requires at least 3 signals; a hard
# filter that dropped repeats could push a batch under that floor and fail
# a scan that had honestly found nothing new -- which would punish the
# system for telling the truth. So the prompt is asked to prefer genuinely
# new items and not to pad, and repeats are COUNTED and surfaced instead
# (ingest_signals_repeats, and repeat_count on the result_ref). A repeat
# rate that stays high is real evidence the horizon or the source list
# needs work, which nothing measured before.

RECENT_SIGNAL_SCAN_LIMIT = 100
RECENT_SIGNAL_HEADLINE_CAP = 40

# Vault signal_type values written by a scan. function 09 emits `signals`,
# the eleven fan-out scanners emit `cards`; both are batches of attributed
# items under a profile topic, so both feed the same cross-run memory.
SIGNAL_BATCH_TYPE = "market_signal_batch"
CARD_BATCH_TYPE = "scanner_card_batch"
# Source-promotion probe results. Not a scan batch -- deliberately
# excluded from SCAN_BATCH_TYPES below so cross-run memory never
# treats a probe as a reported signal.
PROBE_BATCH_TYPE = "source_probe_batch"
SCAN_BATCH_TYPES = frozenset({SIGNAL_BATCH_TYPE, CARD_BATCH_TYPE})


def _batch_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """The attributed items in a scan batch, whichever key its function
    uses -- function 09 says `signals`, the eleven scanners say `cards`."""
    items = payload.get("signals")
    if items is None:
        items = payload.get("cards")
    return items or []


def _parse_iso_timestamp(raw: Any) -> datetime | None:
    """Vault timestamps are RFC 3339; tolerate a trailing Z and treat a
    naive value as UTC. Returns None for anything unparseable rather than
    raising -- a malformed timestamp on one historical row must not sink
    today's scan."""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _already_captured(
    vault: VaultClientExt, sources: dict[str, Any], *, now: datetime | None = None
) -> list[dict[str, str]]:
    """Headline + source_url for every signal this profile already recorded
    inside its own horizon, newest first, capped.

    Matched on the batch's `topic` rather than on a profile id: the signals
    payload is function 09's schema-validated output, whose schema sets
    additionalProperties false, so a profile id cannot be smuggled into it
    -- and topic is unique per profile by construction.

    Never raises. A Vault that is unreachable or slow degrades this scan to
    the cold behaviour it had before this existed, which is worse but not
    broken; failing the scan outright over a missing memory would be a
    worse trade."""
    horizon = timedelta(days=int(sources.get("horizon_days", 30)))
    cutoff = (now or datetime.now(timezone.utc)) - horizon
    captured: list[dict[str, str]] = []
    try:
        rows = vault.list_signals(limit=RECENT_SIGNAL_SCAN_LIMIT)
    except Exception as exc:  # noqa: BLE001 - see docstring: memory is best-effort
        log_event(
            logger,
            logging.WARNING,
            "ingest_signals_memory_unavailable",
            error=sanitize_exception_text(exc),
        )
        return []

    for row in rows:
        payload = row.get("payload") or {}
        if row.get("signal_type") not in SCAN_BATCH_TYPES:
            continue
        if payload.get("topic") != sources["topic"]:
            continue
        received = _parse_iso_timestamp(row.get("received_at") or row.get("created_at"))
        if received is not None and received < cutoff:
            continue
        for signal in _batch_items(payload):
            headline = str(signal.get("headline", "")).strip()
            url = str(signal.get("source_url", "")).strip()
            if headline:
                captured.append({"headline": headline, "source_url": url})
            if len(captured) >= RECENT_SIGNAL_HEADLINE_CAP:
                return captured
    return captured


def _count_repeats(output: dict[str, Any], captured: list[dict[str, str]]) -> int:
    """How many emitted signals restate something already captured, matched
    on source_url first (the same article) and headline second (the same
    story from a re-publication). Measurement only -- nothing is dropped."""
    seen_urls = {item["source_url"] for item in captured if item["source_url"]}
    seen_headlines = {item["headline"].casefold() for item in captured}
    repeats = 0
    for signal in _batch_items(output):
        url = str(signal.get("source_url", "")).strip()
        headline = str(signal.get("headline", "")).strip().casefold()
        if (url and url in seen_urls) or (headline and headline in seen_headlines):
            repeats += 1
    return repeats


def _build_ingest_user_content(
    sources: dict[str, Any],
    fetched: list[dict[str, str]],
    captured: list[dict[str, str]] | None = None,
) -> str:
    lines = [
        f"Topic: {sources['topic']}",
        f"Horizon (days): {sources['horizon_days']}",
    ]
    if captured:
        lines += [
            "",
            "Already captured in this horizon (do not re-report these as new; "
            "prefer genuinely new items, and do not pad to reach the minimum):",
        ]
        lines += [f"- {item['headline']} ({item['source_url']})" for item in captured]
    lines += ["", "Retrieved evidence (fetch_url results, truncated):"]
    for item in fetched:
        lines.append(f"--- SOURCE: {item['url']} ---")
        lines.append(item["body"] or "(empty response body)")
    return "\n".join(lines)

QUIET_SCAN_STATUS = "quiet_scan"


