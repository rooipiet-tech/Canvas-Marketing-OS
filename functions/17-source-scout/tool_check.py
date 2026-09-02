"""Deterministic mock + package-specific rubric checks for function 17.

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

FUNCTION_ID = "17-source-scout"

SOURCE_KINDS = (
    "rss",
    "news-page",
    "vendor-newsroom",
    "tender-portal",
    "regulator",
    "trade-body",
)

# Distinct publishers by construction, so the spread rule has something
# honest to grade. Ordered named-first, mirroring prompt.md's Method:
# a publication the profile itself named is the strongest candidate there
# is, so it leads.
CANDIDATE_POOL = (
    {
        "url": "https://www.itweb.co.za/rss/news",
        "publisher": "ITWeb",
        "source_kind": "rss",
        "rationale": "South African IT trade press, named in this profile's own watchlist note",
    },
    {
        "url": "https://www.moneyweb.co.za/news/tech/feed/",
        "publisher": "Moneyweb",
        "source_kind": "rss",
        "rationale": "South African business and finance coverage this profile's topic asks for",
    },
    {
        "url": "https://www.etenders.gov.za/content/awarded-tenders",
        "publisher": "National Treasury eTenders",
        "source_kind": "tender-portal",
        "rationale": "Public-sector award notices, the tender signal this profile listens for",
    },
    {
        "url": "https://www.businesslive.co.za/rss/bd/companies/",
        "publisher": "BusinessLive",
        "source_kind": "news-page",
        "rationale": "Company-level coverage of the listed groups this profile's buyers sit inside",
    },
)

# A path this mock is RECONSTRUCTING rather than recalling — prompt.md
# rule 4 says a guessed feed path is always low confidence.
GUESSED_URL = "https://www.example-trade-title.co.za/feed/rss.xml"


def _prompt_requires(prompt_text: str, marker: str) -> bool:
    """True when `marker` appears in the prompt, ignoring wrapping and case."""
    normalised = " ".join(prompt_text.split()).lower()
    return " ".join(marker.split()).lower() in normalised


def mock_completion(task: dict, prompt_text: str) -> str:
    """Simulate this function's model output for a golden task, given its prompt."""
    task_input = task.get("input", {})
    profile_id = task_input.get("profile_id", "")
    existing = set(task_input.get("existing_urls") or []) | set(
        task_input.get("existing_candidates") or []
    )

    wants_min_three = _prompt_requires(prompt_text, "at least 3")
    wants_https = _prompt_requires(prompt_text, "uses the secure https scheme")
    wants_no_reproposal = _prompt_requires(prompt_text, "never propose a url that already appears")
    wants_honest_confidence = _prompt_requires(prompt_text, "a feed path you are guessing at")
    wants_spread = _prompt_requires(prompt_text, "spread the list")
    # prompt.md hard rule 10: a `category` entry names no single
    # organisation and has no newsroom. Without the rule in the prompt the
    # mock invents one, so the rubric check exercises prompt.md rather
    # than passing whatever the mock happens to do.
    wants_no_category_newsroom = _prompt_requires(prompt_text, "never propose one for it")

    pool = list(CANDIDATE_POOL)
    if wants_no_reproposal:
        # With the rule present, an already-known source is skipped. Without
        # it, the mock re-proposes one, so the rubric check actually
        # exercises prompt.md rather than always passing.
        pool = [item for item in pool if item["url"] not in existing]
    if not wants_spread:
        pool = [pool[0]] * len(pool) if pool else []

    count = 4 if wants_min_three else 2
    candidates: list[dict[str, object]] = []
    for index in range(min(count, len(pool))):
        item = dict(pool[index])
        if not wants_https:
            item["url"] = item["url"].replace("https://", "http://")
        item["confidence"] = "high" if index == 0 else "medium"
        candidates.append(item)

    # Competitor-owned channels, for a profile that watches a named set.
    # Every one of these is a reconstructed address, so rule 4 makes them
    # low confidence -- and a `category` entry gets no candidate at all
    # unless the prompt has lost rule 10.
    for competitor in task_input.get("competitors") or []:
        kind = competitor.get("kind")
        if kind == "category" and wants_no_category_newsroom:
            continue
        if kind not in ("firm", "category"):
            continue
        slug = "".join(
            char for char in str(competitor.get("name", "")).lower() if char.isalnum()
        )
        candidates.append(
            {
                "url": f"https://www.{slug}.co.za/newsroom",
                "publisher": str(competitor.get("name", "")),
                "source_kind": "vendor-newsroom",
                "rationale": (
                    "This profile watches a named competitor set and its watchlist asks "
                    "for competitor-owned channels"
                ),
                "confidence": "low",
            }
        )

    # The reconstructed path: low only when the prompt carries the rule.
    candidates.append(
        {
            "url": GUESSED_URL,
            "publisher": "A regional trade title",
            "source_kind": "rss",
            "rationale": "Covers this profile's sector, though the feed path is reconstructed",
            "confidence": "low" if wants_honest_confidence else "high",
        }
    )

    return json.dumps({"profile_id": profile_id, "candidates": candidates})


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

    if kind == "all_urls_https":
        bad = [
            c.get("url", "")
            for c in candidates
            if not str(c.get("url", "")).startswith("https://")
        ]
        return (not bad), (f"non-https candidate(s): {bad}" if bad else "every candidate is https")

    if kind == "no_reproposed_source":
        known = set(task_input.get("existing_urls") or []) | set(
            task_input.get("existing_candidates") or []
        )
        repeats = [c.get("url") for c in candidates if c.get("url") in known]
        return (not repeats), (
            f"re-proposed source(s) the caller already has: {repeats}"
            if repeats
            else "no candidate repeats a known source"
        )

    if kind == "guessed_path_is_low_confidence":
        for candidate in candidates:
            if candidate.get("url") == GUESSED_URL:
                ok = candidate.get("confidence") == "low"
                return ok, (
                    "a reconstructed feed path is marked low confidence"
                    if ok
                    else f"reconstructed path claims {candidate.get('confidence')!r} confidence"
                )
        return False, "the reconstructed-path candidate is absent from the output"

    if kind == "distinct_publishers":
        minimum = int(check.get("min", 3))
        publishers = {str(c.get("publisher", "")).strip().lower() for c in candidates}
        publishers.discard("")
        return (len(publishers) >= minimum), (
            f"{len(publishers)} distinct publisher(s), need {minimum}"
        )

    if kind == "source_kinds_in_set":
        bad = [c.get("source_kind") for c in candidates if c.get("source_kind") not in SOURCE_KINDS]
        return (not bad), (f"source_kind outside the fixed set: {bad}" if bad else "all in set")

    if kind == "no_personal_social_profile":
        bad = [
            c.get("url") for c in candidates if "linkedin.com/in/" in str(c.get("url", "")).lower()
        ]
        return (not bad), (f"personal profile proposed as a source: {bad}" if bad else "none")

    if kind == "no_newsroom_for_a_category_competitor":
        categories = {
            str(entry.get("name", "")).strip().lower()
            for entry in (task_input.get("competitors") or [])
            if entry.get("kind") == "category"
        }
        categories.discard("")
        bad = [
            c.get("url")
            for c in candidates
            if str(c.get("publisher", "")).strip().lower() in categories
        ]
        return (not bad), (
            f"proposed a source for a category that names no organisation: {bad}"
            if bad
            else "no candidate claims to be a category's own channel"
        )

    if kind == "hosts_are_resolvable_shape":
        bad = [
            c.get("url")
            for c in candidates
            if not (urlparse(str(c.get("url", ""))).hostname or "")
        ]
        return (not bad), (
            f"candidate with no host: {bad}" if bad else "every candidate has a host"
        )

    return False, f"unknown check kind for {FUNCTION_ID}: {kind!r}"
