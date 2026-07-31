"""Deterministic mock + package-specific rubric checks for function 43.

This package's `mock_completion` runs in one of two modes, distinguished by
the eval task's own input shape:

* **Generative mode** (the normal call shape: `executive_name`, `pillar`,
  `campaign`, optional `sourced_opinion_or_quote`) — simulates the ghostwritten
  post. When no `sourced_opinion_or_quote` is supplied, the mock never
  attributes a personal opinion to the executive; it writes the piece without
  one, exactly as prompt.md's hard rule 1 requires.

* **QA/verdict mode** (the task supplies `draft_text` instead) — simulates
  this function's own never-fabricate check being run over an
  already-written draft, exactly as `functions/02-brand-steward-qa/
  tool_check.py` simulates Brand Steward QA's verdict. This is how the
  golden `fabricated-opinion-blocked` eval proves the rule actually catches
  an unsourced personal-opinion attribution rather than merely stating it in
  prose.
"""

from __future__ import annotations

import json
import re

FUNCTION_ID = "43-executive-ghostwriter"

ROOF_LINE = "Your Data. Delivered."

PILLARS = (
    "Finance-grade trust",
    "Consolidation at scale",
    "Fabric-native",
    "Productised speed",
    "Beyond the dashboard",
)

CFO_VOICE_OPENER = (
    "Finance says one number. Operations says another. Commercial says a third.\n"
    "Your team is not arguing — your systems are. A different number for the "
    "same question is a systems problem wearing a people problem's clothes."
)

NEUTRAL_OPENER = (
    "Reporting takes longer than it should, and the numbers rarely agree on "
    "the first pass."
)

BODY_PARAGRAPH = (
    "The fix is not another dashboard. It is one governed source of truth: "
    "every entity, every ERP, every currency reconciled once, by people who "
    "sign off on numbers for a living. Built by Chartered Accountants, "
    "engineered on Microsoft Fabric."
)

# Phrases that mark a sentence as a first-person opinion attribution, the
# thing that must never appear without a sourced_opinion_or_quote behind it.
OPINION_MARKERS = (
    "i believe",
    "in my view",
    "my take",
    "i've always said",
    "personally, i",
    "my opinion",
    "i think",
    "i'm convinced",
)

VIOLATION_FABRICATED_OPINION = "fabricated-opinion"

# Client and prospect names that may never appear in output while their
# register entry is UNCLEARED. Kept in sync with docs/permission-register.yaml.
GATED_CLIENT_NAMES = (
    "Imperial",
    "Rotork",
    "Weir",
    "ArcelorMittal",
    "SGB Cape",
    "Delta",
)

LINK_SHORTENERS = ("bit.ly", "lnkd.in", "tinyurl.com", "t.co/", "ow.ly", "buff.ly")

URL_RE = re.compile(r"https?://[^\s\"'<>)\\]+")


def _prompt_requires(prompt_text: str, marker: str) -> bool:
    """True when `marker` appears in the prompt, ignoring wrapping and case."""
    normalised = " ".join(prompt_text.split()).lower()
    return " ".join(marker.split()).lower() in normalised


def _has_opinion_marker(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in OPINION_MARKERS)


def _cta_url(campaign: str) -> str:
    return (
        "https://www.canvasintelligence.com/insights/founder-perspective"
        "?utm_source=linkedin&utm_medium=social"
        f"&utm_campaign={campaign}"
    )


def _generate_post(task_input: dict, prompt_text: str) -> str:
    executive_name = str(task_input.get("executive_name", "the executive"))
    pillar = task_input.get("pillar", PILLARS[0])
    campaign = task_input.get("campaign", "general")
    source = str(task_input.get("sourced_opinion_or_quote", "")).strip()

    wants_never_fabricate = _prompt_requires(prompt_text, "never fabricate an opinion")
    wants_roof_line = ROOF_LINE in prompt_text
    wants_verbatim_pillar = _prompt_requires(prompt_text, "use them verbatim")
    wants_utm = _prompt_requires(prompt_text, "utm_campaign")
    wants_cfo_voice = _prompt_requires(prompt_text, "different number for the same question")

    lines = [CFO_VOICE_OPENER if wants_cfo_voice else NEUTRAL_OPENER, ""]

    include_opinion = bool(source) or not wants_never_fabricate
    if include_opinion and source:
        lines.append(f'"{source}" — {executive_name}')
    elif not source:
        lines.append(
            f"{executive_name} has not put a sourced personal stance on record for "
            "this piece yet, so this draft carries no attributed opinion — the facts "
            "below stand on their own."
        )
    lines.append("")
    lines.append(BODY_PARAGRAPH)
    lines.append("")
    if wants_verbatim_pillar:
        lines.append(f"This is the {pillar} pillar in practice.")
    else:
        lines.append("This is what our approach looks like in practice.")
    lines.append("")
    lines.append(
        _cta_url(campaign)
        if wants_utm
        else "https://www.canvasintelligence.com/insights/founder-perspective"
    )
    if wants_roof_line:
        lines.append("")
        lines.append(ROOF_LINE)

    post = "\n".join(lines)

    return json.dumps(
        {"post": post, "pillar": pillar, "cta_url": _cta_url(campaign)},
        indent=2,
        sort_keys=True,
    )


def _verdict_for_draft(task_input: dict, prompt_text: str) -> str:
    draft = str(task_input.get("draft_text", ""))
    source = str(task_input.get("sourced_opinion_or_quote", "")).strip()

    violations: list[str] = []
    reason = "no unsourced opinion attribution found in the draft"

    if _prompt_requires(prompt_text, "never fabricate an opinion") and not source:
        if _has_opinion_marker(draft):
            violations.append(VIOLATION_FABRICATED_OPINION)
            reason = (
                "the draft attributes a personal opinion to the executive with no "
                "sourced_opinion_or_quote supplied — this is a fabricated, unsourced "
                "opinion and must never invent a stance the executive has not actually "
                "stated"
            )

    passed = not violations
    return json.dumps(
        {"pass": passed, "violations": sorted(set(violations)), "reason": reason},
        indent=2,
        sort_keys=True,
    )


def mock_completion(task: dict, prompt_text: str) -> str:
    """Simulate this function's output for a golden task, given its prompt.

    Dispatches on the task's own input shape: `draft_text` present means the
    QA/verdict path (grading an already-written draft); its absence means
    the normal generative path.
    """
    task_input = task.get("input", {})
    if "draft_text" in task_input:
        return _verdict_for_draft(task_input, prompt_text)
    return _generate_post(task_input, prompt_text)


def _post_text(output: str) -> str:
    """The post body, whether the model emitted JSON or bare text."""
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError:
        return output
    if isinstance(parsed, dict) and isinstance(parsed.get("post"), str):
        return parsed["post"]
    return output


def run_check(task: dict, entry: dict, output: str) -> tuple[bool, str]:
    """Package-specific rubric checks. Returns (passed, detail)."""
    check = entry.get("check") or {}
    kind = check.get("kind")

    if kind == "verdict_pass_matches_expected":
        try:
            parsed = json.loads(output)
        except json.JSONDecodeError as exc:
            return False, f"output is not valid JSON ({exc})"
        expected = task.get("verdict", {}).get("pass")
        actual = parsed.get("pass")
        return actual == expected, f"output pass field is {actual!r}, expected {expected!r}"

    if kind == "verdict_violations_include_expected":
        try:
            parsed = json.loads(output)
        except json.JSONDecodeError as exc:
            return False, f"output is not valid JSON ({exc})"
        expected = list(task.get("verdict", {}).get("violations") or [])
        actual = list(parsed.get("violations") or [])
        missing = [code for code in expected if code not in actual]
        return not missing, (
            f"output violations {actual} include all expected {expected}"
            if not missing
            else f"output violations {actual} are missing expected code(s) {missing}"
        )

    post = _post_text(output)

    if kind == "roof_line_is_last_line":
        non_empty = [line.strip() for line in post.splitlines() if line.strip()]
        if not non_empty:
            return False, "post body is empty"
        return (
            non_empty[-1] == ROOF_LINE,
            f"last non-empty line is {non_empty[-1]!r}, expected {ROOF_LINE!r}",
        )

    if kind == "pillar_named_verbatim":
        expected = task.get("input", {}).get("pillar")
        if expected not in PILLARS:
            return False, f"task input pillar {expected!r} is not one of the five pillar names"
        return expected in post, f"post body does not contain the verbatim pillar name {expected!r}"

    if kind == "cta_utm_complete":
        urls = URL_RE.findall(post)
        if len(urls) != 1:
            return False, f"expected exactly 1 URL in the post body, found {len(urls)}: {urls}"
        url = urls[0]
        if not url.startswith("https://www.canvasintelligence.com/"):
            return False, f"CTA URL {url!r} is not a full https://www.canvasintelligence.com/ link"
        missing = [
            param
            for param in ("utm_source=", "utm_medium=", "utm_campaign=")
            if param not in url
        ]
        if missing:
            return False, f"CTA URL is missing UTM parameter(s): {missing}"
        shortener = [name for name in LINK_SHORTENERS if name in post]
        if shortener:
            return False, f"post body contains link shortener(s): {shortener}"
        return True, "exactly one fully qualified CTA URL with all three UTM parameters"

    if kind == "no_gated_client_named":
        found = [name for name in GATED_CLIENT_NAMES if name in output]
        return not found, (
            "no gated client name appears in the output"
            if not found
            else f"output names client(s) that are UNCLEARED in the permission register: {found}"
        )

    if kind == "no_fabricated_opinion":
        source = str(task.get("input", {}).get("sourced_opinion_or_quote", "")).strip()
        if source:
            return True, (
                "task input supplies a sourced opinion; this check only constrains the "
                "no-source path"
            )
        has_marker = _has_opinion_marker(post)
        return not has_marker, (
            "post attributes no personal-opinion phrase to the executive when no "
            "sourced_opinion_or_quote was supplied"
            if not has_marker
            else "post fabricated a personal-opinion attribution despite no "
            "sourced_opinion_or_quote being supplied in the input"
        )

    return False, f"unknown check kind for {FUNCTION_ID}: {kind!r}"
