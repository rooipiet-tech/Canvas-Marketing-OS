"""Deterministic mock + package-specific rubric checks for function 48.

Loaded dynamically by `services/registry/eval_harness.py`. Two jobs:

1. ``mock_completion`` — the deterministic stand-in for the model's reply
   in the default (mocked-gateway) eval mode, derived *from this
   package's own prompt.md*. Each list is consulted only while the prompt
   still describes it, so deleting a list from the prompt makes that
   list's tasks fail rather than silently pass. Four sections are gated
   this way: List A's pillar-specific lead proofs, List C, List D, and
   the revenue-model check. (Lists A-pillar and C only from 2 Sep 2026 —
   see the note above the fact tuples.)
2. ``run_check`` — rubric checks the generic kinds in
   ``services/registry/checks.py`` do not cover.

This package had no evals at all until process 5 (1 Sep 2026) — the one
function in the repository with none, and the gate deciding what may be
published. The tasks alongside this file pin the behaviour the two live
list-gap incidents (rounds 24 and 34, recorded in prompt.md) turned on:
a claim traces, or it does not, and a real cited development from this
week is not fabricated merely because positioning.md has not heard of it.
"""

from __future__ import annotations

import json
import re

FUNCTION_ID = "48-fact-check-verdict"

VIOLATION_FABRICATED = "fabricated-proof-point"
VIOLATION_MISSTATED = "misstated-approved-fact"
VIOLATION_REVENUE = "revenue-model-misstatement"

# A compact stand-in for the standing lists, keyed on the distinctive
# number or phrase a draft would have to restate. Not the whole of Lists
# A-C: enough that a golden task can prove the difference between "this
# traces to a standing fact" and "this traces to nothing".
#
# SPLIT INTO THREE TUPLES, 2 Sep 2026 (sign-off review). They were one
# flat STANDING_FACTS tuple consulted unconditionally, which quietly
# broke this module's own stated contract: the docstring above promises
# "each list is consulted only while the prompt still describes it", and
# that was true of List D and the revenue check alone. List A's
# pillar-specific lead proofs and the whole of List C had no prompt
# coupling at all, so deleting either section from prompt.md left every
# task still passing. Those two sections are precisely the ones added to
# close the rounds 24 and 34 incidents, in which all six Wednesday drafts
# failed in a single live run -- the two regressions least affordable to
# reintroduce were the two nothing was watching. Each tuple is now gated
# on its own section still being present in the prompt.

# List A, company-wide facts (positioning.md section 3). Present in this
# prompt since the first draft.
COMPANY_FACTS = (
    ("99.5", "reconciliation to source"),
    ("2 days", "month-end at least 2 days faster"),
    ("two days", "month-end at least 2 days faster"),
    ("4tb", "Direct Lake at 4TB"),
    ("2013", "founded 2013"),
)

# List A, pillar-specific lead proofs (positioning.md section 5's
# messaging house). Added round 24, 7 Aug 2026, after function 41's
# instruction to lead with the assigned pillar's proof point guaranteed a
# fabricated-proof-point flag for the three pillars the company-wide list
# did not cover.
PILLAR_FACTS = (
    ("40+", "40+ business units, 14+ ERP systems, one governed lakehouse"),
    ("8 entities", "8 entities, 3 countries, 4 currencies"),
    ("first value in a day", "turnkey DaaS platform, first value in a day"),
)

# List C, approved CFO-survey pain language (positioning.md section 4).
# Added round 34, 10 Aug 2026, same failure shape as round 24 for a
# different section: functions 39, 43, 45, 46, 47 and 52 were all told to
# open with attributed survey language the standing lists had never
# covered.
SURVEY_FACTS = (
    ("3 days", "CFO survey: more than 3 days a month lost to reporting"),
    ("three days", "CFO survey: more than 3 days a month lost to reporting"),
)

# Numbers that appear in a standing fact but are commonly sharpened
# upward -- prompt.md's own example is "a week" in place of "3 days".
SHARPENED = ("a week", "a month sooner", "90%", "50 business units", "zero days")

FLAGSHIP_PHRASES = ("flagship", "most differentiated", "our primary business")
BUILDSMART = "buildsmart"


def _prompt_requires(prompt_text: str, marker: str) -> bool:
    """True when `marker` appears in the prompt, ignoring wrapping and case."""
    normalised = " ".join(prompt_text.split()).lower()
    return " ".join(marker.split()).lower() in normalised


def _sentences(draft_text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+|\n+", draft_text) if part.strip()]


def _traces_to_standing(sentence: str, *, pillar_live: bool, survey_live: bool) -> str | None:
    """Match against the standing lists the prompt still carries.

    `pillar_live`/`survey_live` mirror `list_d_live`'s existing gate: a
    section deleted from prompt.md takes its facts out of this mock with
    it, so the golden task that depends on the section fails rather than
    passing on a stand-in the prompt no longer describes.
    """
    lowered = sentence.lower()
    candidates = list(COMPANY_FACTS)
    if pillar_live:
        candidates.extend(PILLAR_FACTS)
    if survey_live:
        candidates.extend(SURVEY_FACTS)
    for token, fact in candidates:
        if token in lowered:
            return fact
    return None


def _traces_to_proof_points(sentence: str, proof_points: list[dict]) -> str | None:
    """A sentence traces to List D when it restates a supplied claim.

    Deliberately conservative and lexical: the shared content words of the
    claim must actually appear in the sentence. A `source` URL alone never
    makes a sentence traceable -- prompt.md is explicit that a source you
    cannot evaluate is not evidence for a claim it does not contain.
    """
    lowered = sentence.lower()
    for point in proof_points:
        claim = str(point.get("claim", ""))
        words = [word for word in re.findall(r"[a-z0-9.+%]+", claim.lower()) if len(word) > 3]
        if not words:
            continue
        matched = sum(1 for word in words if word in lowered)
        if matched >= max(2, len(words) // 2):
            return claim
    return None


# Marketing copy spells small numbers out ("from nine days to two"), so a
# digit-only test silently skips exactly the sentences this check exists
# to judge -- it scored a draft as clean by never looking at it.
NUMBER_WORDS = (
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "eleven", "twelve", "dozen", "half", "double", "triple",
)


def _has_checkable_claim(sentence: str) -> bool:
    """Only judge sentences asserting something checkable -- a number, a
    date, a percentage. Pure framing language is neither traceable nor
    untraceable (prompt.md, "Anything else is out of scope")."""
    if re.search(r"\d", sentence):
        return True
    words = set(re.findall(r"[a-z]+", sentence.lower()))
    return bool(words & set(NUMBER_WORDS))


def mock_completion(task: dict, prompt_text: str) -> str:
    """Simulate this function's verdict for a golden task, given its prompt."""
    task_input = task.get("input", {})
    draft_text = str(task_input.get("draft_text", ""))
    proof_points = list(task_input.get("proof_points") or [])

    list_d_live = _prompt_requires(prompt_text, "List D — This week's cited evidence")
    revenue_live = _prompt_requires(prompt_text, "revenue-model-misstatement")
    pillar_live = _prompt_requires(prompt_text, "Pillar-specific lead proof")
    survey_live = _prompt_requires(prompt_text, "List C — Approved CFO-survey pain language")

    violations: list[str] = []
    notes: list[str] = []

    lowered = draft_text.lower()
    if revenue_live and BUILDSMART in lowered:
        if any(phrase in lowered for phrase in FLAGSHIP_PHRASES):
            violations.append(VIOLATION_REVENUE)
            notes.append(
                "BuildSmart is under 1% of revenue and flat; CoEaaS is the headline offer."
            )

    for sentence in _sentences(draft_text):
        if not _has_checkable_claim(sentence):
            continue
        if any(phrase in sentence.lower() for phrase in SHARPENED):
            violations.append(VIOLATION_MISSTATED)
            notes.append(f"strengthened beyond the approved fact: {sentence!r}")
            continue
        if _traces_to_standing(sentence, pillar_live=pillar_live, survey_live=survey_live):
            continue
        if list_d_live and _traces_to_proof_points(sentence, proof_points):
            continue
        violations.append(VIOLATION_FABRICATED)
        notes.append(f"traces to no approved list: {sentence!r}")

    # Report each code once, in first-seen order.
    deduped: list[str] = []
    for code in violations:
        if code not in deduped:
            deduped.append(code)

    return json.dumps(
        {
            "pass": not deduped,
            "violations": deduped,
            "notes": " | ".join(notes),
        }
    )


def run_check(task: dict, entry: dict, output: str) -> tuple[bool, str]:
    """Package-specific rubric checks."""
    check = entry.get("check") or {}
    kind = check.get("kind")
    verdict = json.loads(output)
    expected = task.get("verdict") or {}

    if kind == "verdict_pass_matches_expected":
        actual = bool(verdict.get("pass"))
        want = bool(expected.get("pass"))
        return actual == want, f"pass={actual}, expected {want}"

    if kind == "verdict_violations_match_expected":
        actual = sorted(verdict.get("violations") or [])
        want = sorted(expected.get("violations") or [])
        return actual == want, f"violations={actual}, expected {want}"

    if kind == "notes_quote_offending_claim":
        needle = str(check.get("text", ""))
        notes = str(verdict.get("notes", ""))
        return needle in notes, (
            f"notes {'quote' if needle in notes else 'do not quote'} {needle!r}"
        )

    if kind == "notes_empty_on_pass":
        notes = str(verdict.get("notes", ""))
        passed = bool(verdict.get("pass"))
        ok = (not passed) or notes == ""
        return ok, f"pass={passed}, notes={notes!r}"

    if kind == "pass_is_true_only_when_no_violations":
        # The schema's own rule, checked as a rubric entry rather than
        # assumed: the review path used to derive `pass` from the
        # violations list instead of reading it, so a verdict that
        # disagreed with itself went unnoticed for as long as nothing
        # compared the two.
        passed = bool(verdict.get("pass"))
        empty = not (verdict.get("violations") or [])
        return passed == empty, f"pass={passed}, violations empty={empty}"

    return False, f"unknown check kind {kind!r} for {FUNCTION_ID}"
