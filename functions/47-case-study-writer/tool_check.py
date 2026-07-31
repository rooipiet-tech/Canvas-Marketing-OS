"""Deterministic mock + package-specific rubric checks for function 47.

Adapted from `functions/42-linkedin-post-writer/tool_check.py`'s
prompt-derived generative pattern, with one deliberate addition: the
`no_gated_client_named` check calls this package's own sibling
`permission_check.check_clearance` directly (mirroring
`functions/26-client-advocacy-harvester/tool_check.py`'s pattern), rather
than relying on a hard-coded tuple of names assumed to always block. This
means the golden `uncleared-client-not-named` eval exercises the real
default-deny code path, not a stated assumption about it.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from functools import lru_cache
from pathlib import Path

FUNCTION_ID = "47-case-study-writer"

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

# Client and prospect names that may never appear in output while their
# register entry is UNCLEARED. Kept in sync with docs/permission-register.yaml.
# Used only as candidate names to scan for; the actual allow/block decision
# always comes from a real permission_check.check_clearance call, never from
# a hard-coded assumption about this tuple.
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


@lru_cache(maxsize=1)
def _load_permission_check():
    """Import the sibling permission_check module without relying on sys.path.

    Keyed on the full resolved path, mirroring function 02's loader, so
    loading this package and any other copy of it in one process cannot
    collide.
    """
    module_path = (Path(__file__).resolve().parent / "permission_check.py").resolve()
    module_name = f"cmos_permission_check__{FUNCTION_ID.replace('-', '_')}"
    if module_name in sys.modules:
        return sys.modules[module_name]

    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


def _prompt_requires(prompt_text: str, marker: str) -> bool:
    """True when `marker` appears in the prompt, ignoring wrapping and case."""
    normalised = " ".join(prompt_text.split()).lower()
    return " ".join(marker.split()).lower() in normalised


def _cta_url(campaign: str) -> str:
    return (
        "https://www.canvasintelligence.com/case-studies/proof-in-practice"
        "?utm_source=web&utm_medium=case-study"
        f"&utm_campaign={campaign}"
    )


def mock_completion(task: dict, prompt_text: str) -> str:
    """Simulate this function's model output for a golden task, given its prompt."""
    task_input = task.get("input", {})
    pillar = task_input.get("pillar", PILLARS[0])
    situation = str(task_input.get("situation", ""))
    approach = str(task_input.get("approach", ""))
    result = str(task_input.get("result", ""))
    campaign = task_input.get("campaign", "general")
    client_reference = str(task_input.get("client_reference", "")).strip()

    wants_cfo_voice = _prompt_requires(prompt_text, "different number for the same question")
    wants_roof_line = ROOF_LINE in prompt_text
    wants_verbatim_pillar = _prompt_requires(prompt_text, "use them verbatim")
    wants_utm = _prompt_requires(prompt_text, "utm_campaign")

    client_named = False
    situation_text = situation
    if client_reference:
        permission_check = _load_permission_check()
        clearance = permission_check.check_clearance(client_reference)
        if clearance.allowed:
            client_named = True
            situation_text = f"{client_reference}: {situation}"
        else:
            # Default deny: write the situation beat client-free rather than
            # ever surfacing the gated name, and never suppress the case
            # study outright — only the naming is blocked.
            situation_text = situation.replace(client_reference, "a JSE-listed multi-entity group")

    lines = [CFO_VOICE_OPENER if wants_cfo_voice else NEUTRAL_OPENER, ""]
    lines.append(f"Situation: {situation_text}")
    lines.append("")
    lines.append(f"Approach: {approach}")
    lines.append("")
    lines.append(f"Result: {result}")
    lines.append("")
    if wants_verbatim_pillar:
        lines.append(f"This case study demonstrates the {pillar} pillar.")
    else:
        lines.append("This case study demonstrates our approach in practice.")
    lines.append("")
    lines.append(
        _cta_url(campaign)
        if wants_utm
        else "https://www.canvasintelligence.com/case-studies/proof-in-practice"
    )
    if wants_roof_line:
        lines.append("")
        lines.append(ROOF_LINE)

    case_study = "\n".join(lines)

    return json.dumps(
        {
            "case_study": case_study,
            "pillar": pillar,
            "cta_url": _cta_url(campaign),
            "client_named": client_named,
        },
        indent=2,
        sort_keys=True,
    )


def _case_study_text(output: str) -> str:
    """The case-study body, whether the model emitted JSON or bare text."""
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError:
        return output
    if isinstance(parsed, dict) and isinstance(parsed.get("case_study"), str):
        return parsed["case_study"]
    return output


def run_check(task: dict, entry: dict, output: str) -> tuple[bool, str]:
    """Package-specific rubric checks. Returns (passed, detail)."""
    check = entry.get("check") or {}
    kind = check.get("kind")
    case_study = _case_study_text(output)

    if kind == "roof_line_is_last_line":
        non_empty = [line.strip() for line in case_study.splitlines() if line.strip()]
        if not non_empty:
            return False, "case study is empty"
        return (
            non_empty[-1] == ROOF_LINE,
            f"last non-empty line is {non_empty[-1]!r}, expected {ROOF_LINE!r}",
        )

    if kind == "pillar_named_verbatim":
        expected = task.get("input", {}).get("pillar")
        if expected not in PILLARS:
            return False, f"task input pillar {expected!r} is not one of the five pillar names"
        return expected in case_study, f"case study does not contain the verbatim pillar name {expected!r}"

    if kind == "cta_utm_complete":
        urls = URL_RE.findall(case_study)
        if len(urls) != 1:
            return False, f"expected exactly 1 URL in the case study, found {len(urls)}: {urls}"
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
        shortener = [name for name in LINK_SHORTENERS if name in case_study]
        if shortener:
            return False, f"case study contains link shortener(s): {shortener}"
        return True, "exactly one fully qualified CTA URL with all three UTM parameters"

    if kind == "no_gated_client_named":
        permission_check = _load_permission_check()
        blob = output
        found: list[str] = []
        for name in GATED_CLIENT_NAMES:
            if name in blob:
                clearance = permission_check.check_clearance(name)
                if not clearance.allowed:
                    found.append(name)
        return not found, (
            "no client name blocked by the real permission_check.check_clearance "
            "default-deny path appears in the output"
            if not found
            else f"output names client(s) that permission_check.check_clearance blocks: {found}"
        )

    return False, f"unknown check kind for {FUNCTION_ID}: {kind!r}"
