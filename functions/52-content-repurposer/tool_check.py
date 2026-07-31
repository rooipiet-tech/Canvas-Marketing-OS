"""Deterministic mock + package-specific rubric checks for function 52.

Adapted from `functions/42-linkedin-post-writer/tool_check.py`'s
prompt-derived pattern. The one structural difference: the simulated output
is an array of derivatives, one per requested `target_formats` entry, and
every rubric check here operates across the whole array rather than a
single post.
"""

from __future__ import annotations

import json
import re

FUNCTION_ID = "52-content-repurposer"

ROOF_LINE = "Your Data. Delivered."

PILLARS = (
    "Finance-grade trust",
    "Consolidation at scale",
    "Fabric-native",
    "Productised speed",
    "Beyond the dashboard",
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

FORMAT_LABELS = {
    "linkedin_post": "LinkedIn post",
    "x_post": "X post",
    "email_teaser": "email teaser",
}


def _prompt_requires(prompt_text: str, marker: str) -> bool:
    """True when `marker` appears in the prompt, ignoring wrapping and case."""
    normalised = " ".join(prompt_text.split()).lower()
    return " ".join(marker.split()).lower() in normalised


def _cta_url(campaign: str, fmt: str) -> str:
    return (
        "https://www.canvasintelligence.com/insights/repurposed-proof"
        f"?utm_source={fmt}&utm_medium=social"
        f"&utm_campaign={campaign}"
    )


def mock_completion(task: dict, prompt_text: str) -> str:
    """Simulate this function's model output for a golden task, given its prompt."""
    task_input = task.get("input", {})
    pillar = task_input.get("pillar", PILLARS[0])
    source_summary = str(task_input.get("source_asset_summary", ""))
    campaign = task_input.get("campaign", "general")
    target_formats = list(task_input.get("target_formats") or ["linkedin_post"])

    wants_roof_line = ROOF_LINE in prompt_text
    wants_verbatim_pillar = _prompt_requires(prompt_text, "use them verbatim")
    wants_utm = _prompt_requires(prompt_text, "utm_campaign")

    derivatives = []
    for fmt in target_formats:
        label = FORMAT_LABELS.get(fmt, fmt)
        lines = [f"{label}: {source_summary}", ""]
        if wants_verbatim_pillar:
            lines.append(f"This is the {pillar} pillar in practice.")
        else:
            lines.append("This is what our approach looks like in practice.")
        lines.append("")
        lines.append(
            _cta_url(campaign, fmt)
            if wants_utm
            else "https://www.canvasintelligence.com/insights/repurposed-proof"
        )
        if wants_roof_line:
            lines.append("")
            lines.append(ROOF_LINE)
        derivatives.append(
            {
                "format": fmt,
                "post": "\n".join(lines),
                "cta_url": _cta_url(campaign, fmt),
            }
        )

    return json.dumps(
        {"derivatives": derivatives, "pillar": pillar}, indent=2, sort_keys=True
    )


def run_check(task: dict, entry: dict, output: str) -> tuple[bool, str]:
    """Package-specific rubric checks. Returns (passed, detail)."""
    check = entry.get("check") or {}
    kind = check.get("kind")

    try:
        parsed = json.loads(output)
    except json.JSONDecodeError as exc:
        return False, f"output is not valid JSON ({exc})"

    derivatives = parsed.get("derivatives", [])

    if kind == "derivative_count_matches_target_formats":
        expected = list(task.get("input", {}).get("target_formats") or [])
        return (
            len(derivatives) == len(expected),
            f"output has {len(derivatives)} derivative(s), expected {len(expected)} "
            f"to match target_formats {expected}",
        )

    if kind == "all_derivatives_roof_lined":
        if not derivatives:
            return False, "output carries no derivatives at all"
        offenders = []
        for derivative in derivatives:
            post = str(derivative.get("post", ""))
            non_empty = [line.strip() for line in post.splitlines() if line.strip()]
            if not non_empty or non_empty[-1] != ROOF_LINE:
                offenders.append(derivative.get("format"))
        return not offenders, (
            "every derivative closes with the roof line"
            if not offenders
            else f"derivative(s) not closing with the roof line: {offenders}"
        )

    if kind == "all_derivatives_pillar_tagged":
        expected = task.get("input", {}).get("pillar")
        if expected not in PILLARS:
            return False, f"task input pillar {expected!r} is not one of the five pillar names"
        offenders = [
            derivative.get("format")
            for derivative in derivatives
            if expected not in str(derivative.get("post", ""))
        ]
        return not offenders, (
            f"every derivative names the pillar {expected!r} verbatim"
            if not offenders
            else f"derivative(s) missing the verbatim pillar name: {offenders}"
        )

    if kind == "all_derivatives_cta_complete":
        offenders = []
        for derivative in derivatives:
            url = str(derivative.get("cta_url", ""))
            post = str(derivative.get("post", ""))
            if not url.startswith("https://www.canvasintelligence.com/"):
                offenders.append(derivative.get("format"))
                continue
            missing = [
                param
                for param in ("utm_source=", "utm_medium=", "utm_campaign=")
                if param not in url
            ]
            if missing or any(shortener in post for shortener in LINK_SHORTENERS):
                offenders.append(derivative.get("format"))
        return not offenders, (
            "every derivative carries a fully qualified CTA URL with all UTM parameters"
            if not offenders
            else f"derivative(s) with an incomplete or shortened CTA URL: {offenders}"
        )

    if kind == "no_gated_client_named":
        found = [name for name in GATED_CLIENT_NAMES if name in output]
        return not found, (
            "no gated client name appears in the output"
            if not found
            else f"output names client(s) that are UNCLEARED in the permission register: {found}"
        )

    return False, f"unknown check kind for {FUNCTION_ID}: {kind!r}"
