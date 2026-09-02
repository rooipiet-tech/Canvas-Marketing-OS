"""Deterministic mock + package-specific rubric checks for function 19.

Loaded dynamically by `services/registry/eval_harness.py` (importlib, keyed
by full package path). Two responsibilities:

1. ``mock_completion`` — the deterministic stand-in for the model's reply in
   the default (mocked-gateway) eval mode. It is derived *from the package's
   own prompt.md*, so removing a rule from the prompt changes the simulated
   output and the corresponding rubric entry fails. That is what makes a
   broken prompt a detectable regression rather than a silent no-op.
2. ``run_check`` — rubric checks specific to this function that the generic
   check kinds in ``services/registry/checks.py`` do not cover.

The mock reads its candidates OUT OF the task's own cards rather than from a
pool of its own, because that is precisely the property under test: a
proposal this function makes is only ever a name one of its input cards
already carries.
"""

from __future__ import annotations

import json
import re

FUNCTION_ID = "19-competitor-scout"

KINDS = ("firm", "product", "category")

# Words that make the organisation before them a BUYER rather than a
# supplier -- prompt.md hard rule 4's verbs, in the order it names them.
BUYER_VERBS = (
    "selected",
    "selects",
    "bought",
    "buys",
    "tendered",
    "migrated",
    "appointed",
    "appoints",
)

# The platforms Canvas is productised on (prompt.md hard rule 5). Never a
# competitor however a card frames them.
PLATFORM_NAMES = ("microsoft", "azure", "power bi", "fabric", "sage")

# A card naming a person, for the rule 6 task. The mock proposes them only
# when the prompt has lost the rule.
PERSON_RE = re.compile(r"\b(?:Mr|Ms|Mrs|Dr)\.?\s+[A-Z][a-z]+|founder\s+([A-Z][a-z]+\s+[A-Z][a-z]+)")


def _prompt_requires(prompt_text: str, marker: str) -> bool:
    """True when `marker` appears in the prompt, ignoring wrapping and case."""
    normalised = " ".join(prompt_text.split()).lower()
    return " ".join(marker.split()).lower() in normalised


def _actor(headline: str) -> str:
    """The organisation a headline is ABOUT, crudely: the capitalised run
    that opens it, or the one after a buyer verb when the opener is the
    buyer. Crude on purpose -- this is a stand-in for a model's reading,
    not an extractor anything ships."""
    for verb in BUYER_VERBS:
        match = re.search(rf"\b{verb}\b\s+(?:the\s+)?([A-Z][\w+]*(?:\s+[A-Z][\w+]*)*)", headline)
        if match:
            return match.group(1).strip()
    match = re.match(r"([A-Z][\w+]*(?:\s+[A-Z][\w+]*)*)", headline.strip())
    return match.group(1).strip() if match else ""


def _buyer(headline: str) -> str:
    """The organisation on the receiving end, when the headline has one."""
    for verb in BUYER_VERBS:
        match = re.match(rf"([A-Z][\w+]*(?:\s+[A-Z][\w+]*)*)\s+\b{verb}\b", headline.strip())
        if match:
            return match.group(1).strip()
    return ""


def _normalise(name: str) -> str:
    """prompt.md hard rule 3's comparison: case-insensitive, ignoring a
    trailing corporate suffix."""
    cleaned = re.sub(r"\s*\((?:pty\)?\s*)?ltd\.?\)?\s*$", "", name.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+(?:ltd\.?|group)\s*$", "", cleaned, flags=re.IGNORECASE)
    return " ".join(cleaned.split()).casefold()


def mock_completion(task: dict, prompt_text: str) -> str:
    """Simulate this function's model output for a golden task, given its prompt."""
    task_input = task.get("input", {})
    horizon = int(task_input.get("horizon_days", 7))
    cards = task_input.get("cards") or []
    known = {_normalise(str(c.get("name", ""))) for c in task_input.get("known_competitors") or []}

    wants_no_padding = _prompt_requires(prompt_text, "never pad")
    wants_traceability = _prompt_requires(
        prompt_text, "never propose a name that appears in no card"
    )
    wants_no_known = _prompt_requires(prompt_text, "never propose a name already in")
    wants_buyer_rule = _prompt_requires(prompt_text, "a buyer is not a competitor")
    wants_platform_rule = _prompt_requires(prompt_text, "are not competitors")
    wants_no_person = _prompt_requires(prompt_text, "never propose a person")

    candidates: list[dict[str, object]] = []
    for card in cards:
        headline = str(card.get("headline", ""))
        name = _actor(headline)

        if wants_buyer_rule:
            buyer = _buyer(headline)
            if buyer and _normalise(name) == _normalise(buyer):
                continue
        else:
            # Without the rule the mock takes whoever opens the headline,
            # which on a buyer-shaped card is the buyer.
            name = _buyer(headline) or name

        person = PERSON_RE.search(headline)
        if person:
            if wants_no_person:
                continue
            name = (person.group(1) or person.group(0)).strip()

        if not name:
            continue
        if wants_platform_rule and any(p in name.casefold() for p in PLATFORM_NAMES):
            continue
        if wants_no_known and _normalise(name) in known:
            continue

        candidates.append(
            {
                "name": name,
                "kind": "firm",
                "evidence_headline": headline,
                "source_url": str(card.get("source_url", "")),
                "rationale": (
                    "Sells data and analytics into the South African groups Canvas sells to"
                ),
                "confidence": "low",
            }
        )

    if not wants_no_padding and not candidates:
        # Without the no-padding rule the mock invents one to avoid an
        # empty list -- which is exactly the behaviour the empty-week task
        # exists to catch.
        candidates.append(
            {
                "name": "A plausible-sounding analytics firm",
                "kind": "firm",
                "evidence_headline": "Not drawn from any card supplied to this run",
                "source_url": "https://example.invalid/invented",
                "rationale": "Proposed to avoid returning an empty candidate list",
                "confidence": "medium",
            }
        )

    if not wants_traceability:
        candidates.append(
            {
                "name": "Recalled From Training",
                "kind": "firm",
                "evidence_headline": "A headline no input card carries",
                "source_url": "https://example.invalid/not-in-any-card",
                "rationale": "Recalled from the model's own knowledge rather than read off a card",
                "confidence": "high",
            }
        )

    return json.dumps({"horizon_days": horizon, "candidates": candidates[:10]})


def run_check(task: dict, entry: dict, output: str) -> tuple[bool, str]:
    """Package-specific rubric checks. Returns (passed, detail)."""
    check = entry.get("check") or {}
    kind = check.get("kind")
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as exc:
        return False, f"output is not valid JSON ({exc})"
    candidates = payload.get("candidates") or []
    task_input = task.get("input", {})
    cards = task_input.get("cards") or []

    if kind == "candidate_count_exactly":
        expected = int(check.get("count", 0))
        return (len(candidates) == expected), (
            f"{len(candidates)} candidate(s), expected exactly {expected}"
        )

    if kind == "every_candidate_traces_to_a_card":
        headlines = {str(c.get("headline", "")) for c in cards}
        urls = {str(c.get("source_url", "")) for c in cards}
        bad = [
            c.get("name")
            for c in candidates
            if str(c.get("evidence_headline", "")) not in headlines
            or str(c.get("source_url", "")) not in urls
        ]
        return (not bad), (
            f"candidate(s) citing evidence no input card carries: {bad}"
            if bad
            else "every candidate quotes a card it was given"
        )

    if kind == "no_known_competitor_reproposed":
        known = {
            _normalise(str(c.get("name", "")))
            for c in task_input.get("known_competitors") or []
        }
        repeats = [c.get("name") for c in candidates if _normalise(str(c.get("name", ""))) in known]
        return (not repeats), (
            f"re-proposed competitor(s) already in the register: {repeats}"
            if repeats
            else "no candidate repeats a known competitor"
        )

    if kind == "no_forbidden_name_proposed":
        forbidden = {str(name).casefold() for name in check.get("names") or []}
        bad = [
            c.get("name")
            for c in candidates
            if str(c.get("name", "")).casefold() in forbidden
        ]
        return (not bad), (
            f"proposed a name this task forbids: {bad}" if bad else "no forbidden name proposed"
        )

    if kind == "kinds_in_set":
        bad = [c.get("kind") for c in candidates if c.get("kind") not in KINDS]
        return (not bad), (f"kind outside the fixed set: {bad}" if bad else "all in set")

    return False, f"unknown check kind for {FUNCTION_ID}: {kind!r}"
