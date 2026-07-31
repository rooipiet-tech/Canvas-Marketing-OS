"""Deterministic mock + package-specific rubric checks for function 39.

Adapted near-verbatim from `functions/42-linkedin-post-writer/tool_check.py`.
The simulated model output is derived from this package's own `prompt.md`:
if a rule is deleted from the prompt, the simulated post loses the
corresponding element and the rubric entry that grades it fails.

The one behavioural difference from function 42: when the caller's
`proof_point` plainly states that no evidence has been documented yet (see
NO_EVIDENCE_MARKERS below), the mock never fabricates a "Proof, not
platitude:" line — this is the AC-18 "missing proof" discipline exercised by
this package's evals.
"""

from __future__ import annotations

import json
import re

FUNCTION_ID = "39-insight-to-story-editor"

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

PROOF_LINE_PREFIX = "Proof, not platitude:"

# Phrases the caller uses in `proof_point` to plainly flag that no evidence
# has been documented yet. Detected case-insensitively; the mock must never
# treat one of these strings as a citable claim.
NO_EVIDENCE_MARKERS = (
    "no evidence",
    "not yet documented",
    "nothing documented",
    "no metric",
    "no product",
    "no client evidence",
    "not documented",
)

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
    """True when `marker` appears in the prompt, ignoring wrapping and case.

    prompt.md is hand-wrapped markdown, so a rule phrase routinely spans a
    line break. Collapsing all runs of whitespace to a single space before
    matching means re-wrapping a paragraph can never silently disable a rule.
    """
    normalised = " ".join(prompt_text.split()).lower()
    return " ".join(marker.split()).lower() in normalised


def _has_evidence(proof_point: str) -> bool:
    lowered = proof_point.lower()
    return not any(marker in lowered for marker in NO_EVIDENCE_MARKERS)


def _cta_url(campaign: str) -> str:
    return (
        "https://www.canvasintelligence.com/insights/story-from-signal"
        "?utm_source=linkedin&utm_medium=social"
        f"&utm_campaign={campaign}"
    )


def mock_completion(task: dict, prompt_text: str) -> str:
    """Simulate this function's model output for a golden task, given its prompt."""
    task_input = task.get("input", {})
    pillar = task_input.get("pillar", PILLARS[0])
    proof_point = str(task_input.get("proof_point", ""))
    campaign = task_input.get("campaign", "general")

    wants_cfo_voice = _prompt_requires(prompt_text, "different number for the same question")
    wants_roof_line = ROOF_LINE in prompt_text
    wants_verbatim_pillar = _prompt_requires(prompt_text, "use them verbatim")
    wants_utm = _prompt_requires(prompt_text, "utm_campaign")
    wants_proof_discipline = _prompt_requires(prompt_text, "proof over platitude")

    evidence_present = _has_evidence(proof_point) if wants_proof_discipline else True

    lines = [CFO_VOICE_OPENER if wants_cfo_voice else NEUTRAL_OPENER, ""]
    lines.append(
        "The fix is not another dashboard. It is one governed source of truth: "
        "every entity, every ERP, every currency reconciled once, by people who "
        "sign off on numbers for a living. Built by Chartered Accountants, "
        "engineered on Microsoft Fabric."
    )
    lines.append("")
    if evidence_present:
        lines.append(f"{PROOF_LINE_PREFIX} {proof_point}")
    else:
        lines.append(
            "No evidence has been documented for this insight yet — this beat is "
            "left out rather than filled with an invented proof point."
        )
    lines.append("")
    if wants_verbatim_pillar:
        lines.append(f"This is the {pillar} pillar in practice.")
    else:
        lines.append("This is what our approach looks like in practice.")
    lines.append("")
    lines.append(
        _cta_url(campaign)
        if wants_utm
        else "https://www.canvasintelligence.com/insights/story-from-signal"
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

    if kind == "post_word_count_between":
        minimum = int(check.get("min", 90))
        maximum = int(check.get("max", 220))
        body_words = [
            word
            for line in post.splitlines()
            if not line.strip().startswith("http")
            for word in line.split()
        ]
        count = len(body_words)
        return minimum <= count <= maximum, (
            f"post body word count is {count}, required range {minimum}-{maximum}"
        )

    if kind == "no_gated_client_named":
        found = [name for name in GATED_CLIENT_NAMES if name in output]
        return not found, (
            "no gated client name appears in the output"
            if not found
            else f"output names client(s) that are UNCLEARED in the permission register: {found}"
        )

    if kind == "missing_proof_fails":
        proof_point = str(task.get("input", {}).get("proof_point", ""))
        if _has_evidence(proof_point):
            return True, "task input carries evidence; this check only constrains the no-evidence path"
        fabricated = PROOF_LINE_PREFIX in post
        return not fabricated, (
            "post carries no fabricated proof-point line when no evidence was documented"
            if not fabricated
            else "post fabricated a proof-point line despite the input plainly stating no "
            "evidence had been documented yet"
        )

    return False, f"unknown check kind for {FUNCTION_ID}: {kind!r}"
