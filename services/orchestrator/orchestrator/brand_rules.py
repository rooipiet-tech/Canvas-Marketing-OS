"""Deterministic backstop for the two fully-enumerable brand-steward QA
rules -- sa-english-spelling and unsupported-claim.

Context (round-31 investigation, F-QA-DETERMINISTIC-BACKSTOP):
functions/02-brand-steward-qa/prompt.md defines 6 QA checks. Of those,
uncleared-client-reference already gets a deterministic cross-check in
dispatch.py's _single_draft_qa_review (round 34's per-draft replacement
for the old _aggregate_qa_review) via permission_check.py. The other five
are LLM-judged only. Two of those five -- sa-english-spelling and
unsupported-claim -- are NOT judgment calls; the prompt spells out an exact
word list and an exact claim-attachment rule, the same lists already used
in tool_check.py's eval-only mock_completion(). A live production run
(2026-08-10, la-weekly-planning-trigger 10:58:22 UTC) blocked all 6 drafts
on these two codes; every draft was independently re-checked against this
exact logic and found clean -- an LLM hallucination on the QA reviewer's
side, not a drafting defect.

This module re-implements just those two checks so _single_draft_qa_review
can drop a violation code the model asserted but the deterministic check
contradicts, the same way uncleared-client-reference is already
cross-checked (only in the opposite direction: today's code can only ADD
that one violation, never remove a hallucinated one).

Deliberately excludes tool_check.py's KNOWN_CLIENT_TOKENS list: that list
is real client names, and per the Marketing project's client-confidentiality
standing rule this module must not carry a reason to exist that requires
naming clients in shipped code.
"""

from __future__ import annotations

import re

SA_SPELLING_VIOLATION = "sa-english-spelling"
UNSUPPORTED_CLAIM_VIOLATION = "unsupported-claim"

# Mirrors functions/02-brand-steward-qa/prompt.md section 3 and
# tool_check.py's US_SPELLINGS -- kept in sync manually; if the prompt's
# word list changes, update this tuple too.
US_SPELLINGS = (
    "productize",
    "productized",
    "productizing",
    "productization",
    "behavior",
    "behaviors",
    "behavioral",
    "organization",
    "organizations",
    "optimize",
    "optimized",
    "optimizing",
    "optimization",
    "analyze",
    "analyzed",
    "analyzing",
    "center",
    "centers",
    "color",
    "colors",
)

# Mirrors functions/02-brand-steward-qa/prompt.md section 6 and
# tool_check.py's UNSUPPORTED_CLAIM_TERMS.
UNSUPPORTED_CLAIM_TERMS = (
    "leading",
    "world-class",
    "best-in-class",
    "the only",
    "unmatched",
    "unrivalled",
    "industry-leading",
    "market-leading",
)

# A superlative is NOT a violation if the same sentence also carries a
# client reference, a number, or a named artefact -- mirrors
# tool_check.py's ARTEFACT_TERMS.
ARTEFACT_TERMS = (
    "case study",
    "reference",
    "report pack",
    "audit",
    "architecture",
    "deck",
)

NUMBER_RE = re.compile(r"\d")

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def has_us_spelling(draft_text: str) -> list[str]:
    """Whole-word, case-insensitive scan for US spelling variants.

    Returns the list of matched terms (empty if none found)."""
    lowered = draft_text.lower()
    return [
        term
        for term in US_SPELLINGS
        if re.search(rf"\b{re.escape(term)}\b", lowered)
    ]


def has_unsupported_claim(draft_text: str) -> list[str]:
    """Per-sentence scan for a superlative with no number or named
    artefact anchored in the same sentence.

    Returns the list of matched (unanchored) terms (empty if none found)."""
    flagged: list[str] = []
    for sentence in _SENTENCE_SPLIT_RE.split(draft_text):
        lowered = sentence.lower()
        for term in UNSUPPORTED_CLAIM_TERMS:
            if term not in lowered:
                continue
            anchored = bool(NUMBER_RE.search(sentence)) or any(
                artefact in lowered for artefact in ARTEFACT_TERMS
            )
            if not anchored:
                flagged.append(term)
    return flagged


def reconcile_violations(
    violations: list[str], draft_text: str
) -> tuple[list[str], list[str]]:
    """Drop sa-english-spelling / unsupported-claim codes the QA model
    asserted but this deterministic check contradicts.

    Only ever REMOVES these two specific codes -- never adds them. The
    model stays free to catch anything this simple word/sentence check
    misses; this only backstops the two rules that are fully enumerable
    per functions/02-brand-steward-qa/prompt.md, so it can't mask a real
    LLM-caught issue in one of the other four checks.

    Returns (reconciled_violations, dropped_codes). dropped_codes is for
    caller-side logging only.
    """
    reconciled = list(violations)
    dropped: list[str] = []

    if SA_SPELLING_VIOLATION in reconciled and not has_us_spelling(draft_text):
        reconciled.remove(SA_SPELLING_VIOLATION)
        dropped.append(SA_SPELLING_VIOLATION)

    if UNSUPPORTED_CLAIM_VIOLATION in reconciled and not has_unsupported_claim(
        draft_text
    ):
        reconciled.remove(UNSUPPORTED_CLAIM_VIOLATION)
        dropped.append(UNSUPPORTED_CLAIM_VIOLATION)

    return reconciled, dropped
