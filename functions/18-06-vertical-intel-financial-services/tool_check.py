"""Deterministic mock + package-specific rubric checks
for function 18-06-vertical-intel-financial-services.

Loaded dynamically by `services/registry/eval_harness.py` (importlib, keyed
by full package path). Mirrors the pattern set by functions/09 and
functions/42: ``mock_completion`` is derived from this package's own
``prompt.md``, so deleting a rule from the prompt changes the simulated
output and the corresponding rubric entry fails - a broken prompt is a
detectable regression, not a silent no-op. ``run_check`` implements only the
check kinds not already covered by ``services/registry/checks.py``'s
GENERIC_KINDS.
"""

from __future__ import annotations

import json
import re
from urllib.parse import urlparse

FUNCTION_ID = "18-06-vertical-intel-financial-services"

TAXONOMY = (
    "cfo-pain-signal",
    "fabric-conversation",
    "sage-ecosystem-signal",
    "tender-signal",
    "vertical-competitor-move",
    "other",
)

# Client names that may never appear in output while their register entry is
# UNCLEARED (docs/permission-register.yaml). Kept in sync with functions/09
# and functions/42's convention - never a competitor or vendor name.
GATED_CLIENT_NAMES = (
    "Imperial",
    "Rotork",
    "Weir",
    "ArcelorMittal",
    "SGB Cape",
    "Delta",
)

# Common aliases for the gated names above (already substring-covered by
# GATED_CLIENT_NAMES, kept explicit for readability/documentation).
GATED_CLIENT_ALIASES = (
    "Imperial Logistics",
    "ArcelorMittal SA",
)

# Lightweight heuristic for the personal-data/named-individual guardrail
# (AC-42): flags an honorific + capitalised name (e.g. "Mr Ndlovu", "Dr Jane
# van der Merwe"). Deliberately a simple marker-based heuristic consistent
# with this repo's mocked-eval idiom, not a real NLP NER system.
INDIVIDUAL_NAME_PATTERN = re.compile(
    r"\b(?:Mr|Mrs|Ms|Dr|Prof)\.?\s+[A-Z][a-zA-Z'-]+(?:\s+[A-Z][a-zA-Z'-]+){0,2}\b"
)

HEADLINES = (
    "A financial-services group referenced a live Fabric-native "
    "regulatory-reporting implementation in a public briefing",
    "A regulatory-reporting consolidation tender for a "
    "financial-services group scored Fabric-native delivery as preferred",
    "A financial-services analytics vendor announced a regulatory-reporting module update",
    "A financial-services CFO's public remarks cited "
    "reconciliation-cycle pain consistent with the CFO survey language",
)

SO_WHATS = (
    "A live Fabric-native regulatory implementation mirrors "
    "Canvas's own Fabric-native pillar proof for this vertical",
    "A tender preferring Fabric-native delivery validates "
    "this vertical's demand for exactly Canvas's positioning",
    "A regulatory-reporting module update is a vertical-competitor "
    "signal worth tracking against Canvas's own proof",
    "A CFO quote on reconciliation-cycle pain is CFO-office "
    "pain language worth citing verbatim in a proof post",
)

SOURCE_POOL = (
    "https://www.businesslive.co.za/financial-services",
    "https://www.itweb.co.za/article/finserv-tenders",
    "https://learn.microsoft.com/fabric/whats-new",
    "https://www.etenders.gov.za/awards",
)

IS_VERTICAL = True
VERTICAL_CONST = "Financial Services"


def _prompt_requires(prompt_text: str, marker: str) -> bool:
    """True when `marker` appears in the prompt, ignoring wrapping and case."""
    normalised = " ".join(prompt_text.split()).lower()
    return " ".join(marker.split()).lower() in normalised


def mock_completion(task: dict, prompt_text: str) -> str:
    """Simulate this function's model output for a golden task, given its prompt."""
    task_input = task.get("input", {})
    topic = task_input.get("topic", "")
    horizon = int(task_input.get("horizon_days", 30))
    thin = bool(task_input.get("thin_evidence", False))
    client_ref = task_input.get("client_reference")
    individual_ref = task_input.get("individual_reference")

    wants_sources = _prompt_requires(prompt_text, "source_url")
    wants_card_type = _prompt_requires(prompt_text, "card_type")
    wants_taxonomy = _prompt_requires(prompt_text, "taxonomy")
    wants_evidence_grade = _prompt_requires(prompt_text, "evidence_grade")
    wants_confidence = _prompt_requires(prompt_text, "confidence")
    wants_min_three = _prompt_requires(prompt_text, "at least 3")
    wants_horizon_echo = _prompt_requires(prompt_text, "repeats the horizon")
    wants_no_client_rule = _prompt_requires(prompt_text, "never name a client")
    wants_no_individual_rule = _prompt_requires(prompt_text, "never name a specific individual")
    wants_domain_diversity = _prompt_requires(prompt_text, "distinct domains")
    wants_proof_light = _prompt_requires(prompt_text, "proof-light")

    count = 4 if wants_min_three else 2
    seeds = task_input.get("sources") or []

    cards: list[dict[str, object]] = []
    for index in range(count):
        card: dict[str, object] = {
            "headline": HEADLINES[index % len(HEADLINES)],
            "so_what": SO_WHATS[index % len(SO_WHATS)],
        }
        if client_ref and not wants_no_client_rule:
            # Simulates a broken prompt: with the no-client-name rule
            # missing, an uncleared client name leaks straight through. This
            # is what proves the no_gated_client_named check actually
            # exercises prompt.md rather than always passing.
            card["headline"] = f"{client_ref} is evaluating a new vendor this quarter"
        if individual_ref and not wants_no_individual_rule:
            # Mirrors the client-leak simulation above: with the
            # personal-data/named-individual rule missing, a caller-
            # supplied individual's name leaks straight through as an
            # honorific + name, proving no_named_individual actually
            # exercises prompt.md rather than always passing.
            card["headline"] = f"Mr {individual_ref} was appointed as the new practice lead"
        if wants_sources:
            if wants_domain_diversity and index < len(seeds):
                card["source_url"] = seeds[index]
            else:
                card["source_url"] = SOURCE_POOL[index % len(SOURCE_POOL)]
        if wants_card_type:
            card["card_type"] = "threat" if index % 2 == 0 else "opportunity"
        if wants_taxonomy:
            card["taxonomy"] = TAXONOMY[index % len(TAXONOMY)]
        if wants_evidence_grade:
            if thin or wants_proof_light:
                card["evidence_grade"] = "light"
            else:
                card["evidence_grade"] = ["moderate", "strong", "light", "moderate"][index % 4]
        if wants_confidence:
            card["confidence"] = "low" if thin else ("high" if index == 0 else "medium")
        cards.append(card)

    if wants_horizon_echo:
        summary = (
            f"Across the last {horizon} days the {topic} scan moved on "
            "attribution and change: retrievable third-party evidence now "
            "supports the leading cards, while thin or vendor-self-reported "
            "material is marked as such rather than rounded up."
        )
    else:
        summary = (
            f"The {topic} scan moved on attribution and change, with "
            "third-party evidence supporting the leading cards and thin "
            "material left unverified."
        )

    result: dict[str, object] = {
        "topic": topic,
        "horizon_days": horizon,
        "summary": summary,
        "cards": cards,
    }
    if IS_VERTICAL:
        result["vertical"] = VERTICAL_CONST

    return json.dumps(result, indent=2, sort_keys=True)


def _parse(output: str) -> dict:
    return json.loads(output)


def run_check(task: dict, entry: dict, output: str) -> tuple[bool, str]:
    """Package-specific rubric checks. Returns (passed, detail)."""
    check = entry.get("check") or {}
    kind = check.get("kind")

    if kind == "distinct_source_domains":
        minimum = int(check.get("min", 2))
        try:
            cards = _parse(output).get("cards", [])
        except json.JSONDecodeError as exc:
            return False, f"output is not valid JSON ({exc})"
        domains = {
            urlparse(str(card.get("source_url", ""))).netloc
            for card in cards
            if card.get("source_url")
        }
        domains.discard("")
        return (
            len(domains) >= minimum,
            f"{len(domains)} distinct source domain(s), need >= {minimum}: {sorted(domains)}",
        )

    if kind == "every_card_has_https_source":
        try:
            cards = _parse(output).get("cards", [])
        except json.JSONDecodeError as exc:
            return False, f"output is not valid JSON ({exc})"
        if not cards:
            return False, "no cards emitted"
        missing = [
            index
            for index, card in enumerate(cards)
            if not str(card.get("source_url", "")).startswith("https://")
        ]
        return not missing, (
            "every card carries an https source_url"
            if not missing
            else f"card index(es) {missing} lack an https:// source_url"
        )

    if kind == "card_type_in_set":
        try:
            cards = _parse(output).get("cards", [])
        except json.JSONDecodeError as exc:
            return False, f"output is not valid JSON ({exc})"
        if not cards:
            return False, "no cards emitted"
        bad = [
            str(card.get("card_type"))
            for card in cards
            if card.get("card_type") not in ("opportunity", "threat")
        ]
        return not bad, (
            "every card is tagged opportunity or threat"
            if not bad
            else f"card_type value(s) outside the fixed set: {bad}"
        )

    if kind == "taxonomy_in_set":
        try:
            cards = _parse(output).get("cards", [])
        except json.JSONDecodeError as exc:
            return False, f"output is not valid JSON ({exc})"
        if not cards:
            return False, "no cards emitted"
        bad = [str(card.get("taxonomy")) for card in cards if card.get("taxonomy") not in TAXONOMY]
        return not bad, (
            "every card's taxonomy is one of the fixed set"
            if not bad
            else f"taxonomy value(s) outside the fixed set {TAXONOMY}: {bad}"
        )

    if kind == "evidence_grade_in_set":
        try:
            cards = _parse(output).get("cards", [])
        except json.JSONDecodeError as exc:
            return False, f"output is not valid JSON ({exc})"
        if not cards:
            return False, "no cards emitted"
        bad = [
            str(card.get("evidence_grade"))
            for card in cards
            if card.get("evidence_grade") not in ("strong", "moderate", "light")
        ]
        return not bad, (
            "every card's evidence_grade is strong, moderate or light"
            if not bad
            else f"evidence_grade value(s) outside the fixed set: {bad}"
        )

    if kind == "no_gated_client_named":
        found = [name for name in GATED_CLIENT_NAMES if name in output]
        found += [alias for alias in GATED_CLIENT_ALIASES if alias in output and alias not in found]
        return not found, (
            "no gated client name appears in the output"
            if not found
            else f"output names client(s) that are UNCLEARED in the permission register: {found}"
        )

    if kind == "no_named_individual":
        honorific_hits = sorted(set(INDIVIDUAL_NAME_PATTERN.findall(output)))
        individual_ref = str((task.get("input") or {}).get("individual_reference") or "")
        ref_hit = bool(individual_ref) and individual_ref in output
        hits = honorific_hits + ([individual_ref] if ref_hit else [])
        return not hits, (
            "no named individual detected in the output"
            if not hits
            else f"output appears to name a specific individual: {hits}"
        )

    return False, f"unknown check kind for {FUNCTION_ID}: {kind!r}"
