"""Deterministic mock + package-specific rubric checks for function 46.

Adapted from `functions/42-linkedin-post-writer/tool_check.py`'s
prompt-derived pattern: the simulated model output is derived from this
package's own `prompt.md`, so deleting a rule from the prompt (for example
the roof line) causes the simulated newsletter body to lose the
corresponding element and the rubric entry that grades it to fail.
"""

from __future__ import annotations

import json
import re

FUNCTION_ID = "46-newsletter-writer"

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


def _cta_url(campaign: str) -> str:
    return (
        "https://www.canvasintelligence.com/insights/weekly-proof-digest"
        "?utm_source=email&utm_medium=newsletter"
        f"&utm_campaign={campaign}"
    )


def mock_completion(task: dict, prompt_text: str) -> str:
    """Simulate this function's model output for a golden task, given its prompt."""
    task_input = task.get("input", {})
    pillar = task_input.get("pillar", PILLARS[0])
    proof_points = list(task_input.get("proof_points") or [])
    campaign = task_input.get("campaign", "general")

    wants_cfo_voice = _prompt_requires(prompt_text, "different number for the same question")
    wants_roof_line = ROOF_LINE in prompt_text
    wants_verbatim_pillar = _prompt_requires(prompt_text, "use them verbatim")
    wants_utm = _prompt_requires(prompt_text, "utm_campaign")

    lines = [CFO_VOICE_OPENER if wants_cfo_voice else NEUTRAL_OPENER, ""]
    lines.append(
        "The fix is not another dashboard. It is one governed source of truth: "
        "every entity, every ERP, every currency reconciled once, by people who "
        "sign off on numbers for a living. Built by Chartered Accountants, "
        "engineered on Microsoft Fabric."
    )
    lines.append("")
    for index, proof_point in enumerate(proof_points, start=1):
        lines.append(f"{PROOF_LINE_PREFIX} {proof_point}")
        lines.append("")
    if wants_verbatim_pillar:
        lines.append(f"This issue leads with the {pillar} pillar.")
    else:
        lines.append("This issue leads with our approach in practice.")
    lines.append("")
    lines.append(
        _cta_url(campaign)
        if wants_utm
        else "https://www.canvasintelligence.com/insights/weekly-proof-digest"
    )
    if wants_roof_line:
        lines.append("")
        lines.append(ROOF_LINE)

    body = "\n".join(lines)
    subject = f"{pillar}: this week's proof, not platitude"

    return json.dumps(
        {"subject": subject, "body": body, "cta_url": _cta_url(campaign)},
        indent=2,
        sort_keys=True,
    )


def _body_text(output: str) -> str:
    """The newsletter body, whether the model emitted JSON or bare text."""
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError:
        return output
    if isinstance(parsed, dict) and isinstance(parsed.get("body"), str):
        return parsed["body"]
    return output


def run_check(task: dict, entry: dict, output: str) -> tuple[bool, str]:
    """Package-specific rubric checks. Returns (passed, detail)."""
    check = entry.get("check") or {}
    kind = check.get("kind")
    body = _body_text(output)

    if kind == "roof_line_is_last_line":
        non_empty = [line.strip() for line in body.splitlines() if line.strip()]
        if not non_empty:
            return False, "newsletter body is empty"
        return (
            non_empty[-1] == ROOF_LINE,
            f"last non-empty line is {non_empty[-1]!r}, expected {ROOF_LINE!r}",
        )

    if kind == "pillar_named_verbatim":
        expected = task.get("input", {}).get("pillar")
        if expected not in PILLARS:
            return False, f"task input pillar {expected!r} is not one of the five pillar names"
        return expected in body, f"newsletter body does not contain the verbatim pillar name {expected!r}"

    if kind == "cta_utm_complete":
        urls = URL_RE.findall(body)
        if len(urls) != 1:
            return False, f"expected exactly 1 URL in the newsletter body, found {len(urls)}: {urls}"
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
        shortener = [name for name in LINK_SHORTENERS if name in body]
        if shortener:
            return False, f"newsletter body contains link shortener(s): {shortener}"
        return True, "exactly one fully qualified CTA URL with all three UTM parameters"

    if kind == "no_gated_client_named":
        found = [name for name in GATED_CLIENT_NAMES if name in output]
        return not found, (
            "no gated client name appears in the output"
            if not found
            else f"output names client(s) that are UNCLEARED in the permission register: {found}"
        )

    return False, f"unknown check kind for {FUNCTION_ID}: {kind!r}"
