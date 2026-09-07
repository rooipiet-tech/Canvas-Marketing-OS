from __future__ import annotations

from typing import Any

from . import core
from .handlers.canva import CAROUSEL_BULK_CSV_MARKER


def _teams_display_text(draft_text: str) -> str:
    """Strips _render_carousel's appended Canva bulk-upload CSV block
    before draft_text feeds a Teams excerpt or a diff between retry
    attempts (F-CAROUSEL-CSV-LEAKS-INTO-TEAMS, 15 Aug 2026). The CSV is
    machine-oriented (slide/image/template-id columns for Canva's bulk
    creator) and no code anywhere parses it back out of draft_text --
    confirmed via repo-wide search, only _render_carousel ever writes it
    -- so it is safe to drop for display purposes. Left in place for
    non-carousel draft_text (the marker is absent, find() returns -1,
    text passes through unchanged) and for every OTHER consumer of the
    stored draft_text (Vault, the console review page, the approval
    inbox) -- only what feeds Teams notify is touched here. Confirmed
    live: Pieter's screenshots (15 Aug) of a QA-retry-exhausted carousel
    card showed raw CSV rows and unified-diff hunk headers dumped
    verbatim into the "excerpt"/"track changes" TextBlocks -- unreadable
    to a human reviewer -- because the pre-fix diff/excerpt ran over the
    full CSV-appended text instead of just the slide copy."""
    idx = draft_text.find(CAROUSEL_BULK_CSV_MARKER)
    return draft_text[:idx].rstrip() if idx != -1 else draft_text

DRAFT_REVIEW_CHANNELS = {
    "draft-insight-to-story": "linkedin",
    "draft-executive-ghostwrite": "linkedin",
    "draft-carousel-post": "linkedin",
    "draft-newsletter": "email",
    # Function 47's own prompt.md sets a human-initiated cadence and the
    # loop excludes it from Friday's auto-schedule: a case study is a
    # web/deck asset, never a social post.
    "draft-case-study": "web",
    # Function 52 produces linkedin_post / x_post / email_teaser
    # derivatives in one asset, so no single channel is truthful. The
    # social shape is the majority and the strictest common denominator
    # of the three, and both branch identically under function 02 today.
    "draft-content-repurpose": "linkedin",
}


def _review_channel(draft_task_type: str | None) -> str:
    return DRAFT_REVIEW_CHANNELS.get(draft_task_type or "", "linkedin")


def _reviewable_draft_text(draft_text: str) -> str:
    """The copy a reviewer should actually judge.

    Strips _render_carousel's appended Canva bulk-create CSV -- machine
    columns (slide_number/headline/subhead/image_ref/brand_template_id),
    not prose. Nothing is lost to review by dropping it: every cell in
    that manifest is generated from the slide headlines and subheads that
    remain in the text above it, so the same words are still checked,
    once. Left as-is for every other draft type (the marker is absent and
    the text passes through unchanged).

    Same reasoning that already applies to the Teams excerpt -- see
    _teams_display_text, whose incident note records what raw CSV rows
    look like to a human reader."""
    return _teams_display_text(draft_text)


def _resolve_verdict(
    function_id: str,
    verdict: dict[str, Any],
) -> tuple[bool, list[str]]:
    """Reads a QA function's verdict as the contract defines it.

    F-QA-VERDICT-FAIL-OPEN. Both review paths did
    `violations = list(verdict.get("violations") or [])` and then
    `passed = not violations`, never reading the `pass` field at all,
    while neither function 02 nor function 48 had its output validated
    anywhere -- the only stage in the pipeline with no contract
    enforcement on either side, and the one that decides what may be
    published. Two responses therefore passed content through unreviewed:

      * `{}` -- every required field missing -- scored zero violations
        and published.
      * `{"pass": false, "violations": [], "notes": "..."}` -- an explicit
        refusal with the reason in prose rather than as a code -- was
        overridden into a pass.

    Validating the output makes the first impossible (all three fields
    are required). This returns the model's own declared verdict
    alongside its codes so the caller can hold them to the schema's own
    rule -- "pass is true only when violations is empty" -- instead of
    re-deriving one from the other.
    """
    core._validate_function_output(function_id, verdict)
    return bool(verdict.get("pass")), list(verdict.get("violations") or [])


# Recorded on the result_ref when a QA function declares a failure but
# names no violation code. Not one of function 02's or 48's own enum
# codes -- it is the orchestrator's account of a self-inconsistent
# verdict, and naming it separately keeps it out of the retry loop's
# recipe matching and legible to whoever reads the blocked card.
QA_VERDICT_UNSPECIFIED_FAILURE = "verdict-declared-failure-without-code"

# TD-35. functions/48-fact-check-verdict/prompt.md carries a note claiming
# Pieter signed it off as settled QA policy on 2 Sep 2026. That note was
# written by an engineering session and this repository has no way to
# confirm the review it describes actually happened -- so neither this
# code nor a human reading it should treat the prompt's own prose as
# evidence of approval. policies/fact-check-gate.yaml's `approved` flag is
# the one thing consulted instead; it defaults to false. See
# functions/48-fact-check-verdict/REVIEW-PACKET.md.
