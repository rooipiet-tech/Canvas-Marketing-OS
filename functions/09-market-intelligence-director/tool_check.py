"""Deterministic mock + package-specific rubric checks for function 09.

Loaded dynamically by `services/registry/eval_harness.py` (importlib, keyed
by full package path). Two responsibilities:

1. ``mock_completion`` — the deterministic stand-in for the model's reply in
   the default (mocked-gateway) eval mode. It is derived *from the package's
   own prompt.md*, so removing a rule from the prompt changes the simulated
   output and the corresponding rubric entry fails. That is what makes a
   broken prompt a detectable regression rather than a silent no-op.
2. ``run_check`` — rubric checks specific to this function that the generic
   check kinds in ``services/registry/checks.py`` do not cover.
"""

from __future__ import annotations

import json
from urllib.parse import urlparse

FUNCTION_ID = "09-market-intelligence-director"

PILLARS = (
    "Finance-grade trust",
    "Consolidation at scale",
    "Fabric-native",
    "Productised speed",
    "Beyond the dashboard",
)

# Fixed, deterministic source pool. Distinct domains by construction so the
# domain-diversity rule has something honest to grade.
SOURCE_POOL = (
    "https://learn.microsoft.com/fabric/whats-new",
    "https://techcommunity.microsoft.com/fabric-blog",
    "https://www.gartner.com/en/documents/data-management",
    "https://www.moneyweb.co.za/news/industry",
    "https://www.bdo.global/insights/finance-transformation",
)

HEADLINES = (
    "Microsoft shipped a Direct Lake change affecting large semantic models",
    "A Southern African listed group tendered for multi-ERP consolidation",
    "Finance teams report reconciliation cycles above three days per month",
    "A vendor published a self-reported Fabric migration benchmark",
    "Exception-based alerting displaced scheduled reporting in a survey",
)

SO_WHATS = (
    "Affects how we size Direct Lake workloads in proposals",
    "A live consolidation tender matches our referenced delivery pattern",
    "Mirrors the CFO pain our positioning already leads with",
    "Unverified vendor claim; usable only as a directional indicator",
    "Supports the beyond-the-dashboard narrative with third-party evidence",
)


def _prompt_requires(prompt_text: str, marker: str) -> bool:
    """True when `marker` appears in the prompt, ignoring wrapping and case.

    prompt.md is hand-wrapped markdown, so a rule phrase routinely spans a
    line break. Collapsing all runs of whitespace to a single space before
    matching means re-wrapping a paragraph can never silently disable a rule.
    """
    normalised = " ".join(prompt_text.split()).lower()
    return " ".join(marker.split()).lower() in normalised


def mock_completion(task: dict, prompt_text: str) -> str:
    """Simulate this function's model output for a golden task, given its prompt."""
    task_input = task.get("input", {})
    topic = task_input.get("topic", "")
    horizon = int(task_input.get("horizon_days", 30))
    thin = bool(task_input.get("thin_evidence", False))

    wants_sources = _prompt_requires(prompt_text, "source_url")
    wants_pillars = _prompt_requires(prompt_text, "pillar")
    wants_confidence = _prompt_requires(prompt_text, "confidence")
    # Tracks prompt.md hard rule 1, which used to read "Return **at least
    # 3** signals and at most 8" and now reads "3 to 8 on an ordinary
    # day". The wording changed because rule 1 and hard rule 9 ("never pad
    # the batch back up to the minimum") contradicted each other: on a day
    # with fewer than three attributable new signals the model could only
    # pad or fail, and schema.json's minItems 3 made falling short
    # dead-letter the whole loop. minItems is now 1 (F-INGEST-QUIET-SCAN).
    #
    # These golden tasks describe an ORDINARY scan, so 3 remains the right
    # expectation for them -- what changed is that it is no longer a hard
    # floor, and the quiet-day path is covered by dispatch-level tests
    # rather than here.
    #
    # The coupling is deliberate and worth keeping: reading the count out
    # of the prompt is what makes these evals prove the prompt says what
    # they check, rather than checking a constant that could drift away
    # from it. Re-wording rule 1 SHOULD break this and make someone look.
    wants_ordinary_three = _prompt_requires(prompt_text, "3 to 8 on an ordinary day")
    wants_horizon_echo = _prompt_requires(prompt_text, "repeats the horizon")

    count = 4 if wants_ordinary_three else 2
    seeds = task_input.get("sources") or []

    signals = []
    for index in range(count):
        signal: dict[str, object] = {
            "headline": HEADLINES[index % len(HEADLINES)],
            "so_what": SO_WHATS[index % len(SO_WHATS)],
        }
        if wants_sources:
            if index < len(seeds):
                signal["source_url"] = seeds[index]
            else:
                signal["source_url"] = SOURCE_POOL[index % len(SOURCE_POOL)]
        if wants_pillars:
            signal["pillar"] = PILLARS[index % len(PILLARS)]
        if wants_confidence:
            signal["confidence"] = "low" if thin else ("high" if index == 0 else "medium")
        signals.append(signal)

    if wants_horizon_echo:
        summary = (
            f"Across the last {horizon} days the {topic} conversation moved on "
            "attribution and consolidation: retrievable third-party evidence now "
            "supports the reconciliation and exception-management narratives, while "
            "vendor self-reported benchmarks remain unverified and are marked as such."
        )
    else:
        summary = (
            f"The {topic} conversation moved on attribution and consolidation, with "
            "third-party evidence supporting the reconciliation narrative and vendor "
            "self-reported benchmarks left unverified."
        )

    return json.dumps(
        {
            "topic": topic,
            "horizon_days": horizon,
            "summary": summary,
            "signals": signals,
        },
        indent=2,
        sort_keys=True,
    )


def _parse(output: str) -> dict:
    return json.loads(output)


def run_check(task: dict, entry: dict, output: str) -> tuple[bool, str]:
    """Package-specific rubric checks. Returns (passed, detail)."""
    check = entry.get("check") or {}
    kind = check.get("kind")

    if kind == "distinct_source_domains":
        minimum = int(check.get("min", 2))
        try:
            signals = _parse(output).get("signals", [])
        except json.JSONDecodeError as exc:
            return False, f"output is not valid JSON ({exc})"
        domains = {
            urlparse(str(signal.get("source_url", ""))).netloc
            for signal in signals
            if signal.get("source_url")
        }
        domains.discard("")
        return (
            len(domains) >= minimum,
            f"{len(domains)} distinct source domain(s), need >= {minimum}: {sorted(domains)}",
        )

    if kind == "every_signal_has_https_source":
        try:
            signals = _parse(output).get("signals", [])
        except json.JSONDecodeError as exc:
            return False, f"output is not valid JSON ({exc})"
        if not signals:
            return False, "no signals emitted"
        missing = [
            index
            for index, signal in enumerate(signals)
            if not str(signal.get("source_url", "")).startswith("https://")
        ]
        return not missing, (
            "every signal carries an https source_url"
            if not missing
            else f"signal index(es) {missing} lack an https:// source_url"
        )

    if kind == "every_signal_pillar_in_set":
        try:
            signals = _parse(output).get("signals", [])
        except json.JSONDecodeError as exc:
            return False, f"output is not valid JSON ({exc})"
        if not signals:
            return False, "no signals emitted"
        bad = [
            str(signal.get("pillar"))
            for signal in signals
            if signal.get("pillar") not in PILLARS
        ]
        return not bad, (
            "every signal is tagged with one of the five exact pillar names"
            if not bad
            else f"pillar value(s) outside the fixed set of five: {bad}"
        )

    if kind == "confidence_low_when_thin":
        try:
            parsed = _parse(output)
        except json.JSONDecodeError as exc:
            return False, f"output is not valid JSON ({exc})"
        signals = parsed.get("signals", [])
        if not signals:
            return False, "no signals emitted"
        thin = bool(task.get("input", {}).get("thin_evidence", False))
        if not thin:
            return True, "task input is not flagged thin_evidence; rule not engaged"
        not_low = [
            index
            for index, signal in enumerate(signals)
            if signal.get("confidence") != "low"
        ]
        return not not_low, (
            "every signal on thin evidence is marked confidence=low"
            if not not_low
            else f"signal index(es) {not_low} rounded thin evidence above low"
        )

    return False, f"unknown check kind for {FUNCTION_ID}: {kind!r}"
