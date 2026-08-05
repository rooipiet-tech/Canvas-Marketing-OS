"""Deterministic mock + package-specific rubric checks for function 02.

The simulated verdict is produced by actually running this package's
`permission_check.check_clearance` against `docs/permission-register.yaml`,
plus a compact copy of the deterministic brand rules. So the golden eval for
an uncleared client reference exercises the real default-deny code path, not
a hard-coded string.

`mock_completion` is prompt-derived: each check runs only if the package's
own `prompt.md` still instructs it. Deleting the permission-register section
from the prompt therefore makes the uncleared-client task fail, which is the
behaviour a prompt regression must have.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import sys
from functools import lru_cache
from pathlib import Path

FUNCTION_ID = "02-brand-steward-qa"

VIOLATION_UNCLEARED = "uncleared-client-reference"
VIOLATION_SHORTENER = "link-shortener"
VIOLATION_SPELLING = "sa-english-spelling"
VIOLATION_MISSING_CTA = "missing-cta"
VIOLATION_URL_UTM = "url-utm"
VIOLATION_UNSUPPORTED = "unsupported-claim"

LINK_SHORTENERS = ("bit.ly", "lnkd.in", "tinyurl.com", "ow.ly", "buff.ly", "goo.gl")

US_SPELLINGS = (
    "productize",
    "productized",
    "productizing",
    "productization",
    "behavior",
    "behaviors",
    "behavioral",
    "organization",
    "optimize",
    "analyze",
    "center",
)

# Names that may be referenced in a draft. Clearance itself is decided by
# permission_check against the register — this list only tells the mock which
# tokens to look for in free text.
KNOWN_CLIENT_TOKENS = (
    "Imperial Logistics",
    "Imperial",
    "Rotork",
    "Weir",
    "ArcelorMittal SA",
    "ArcelorMittal",
    "SGB Cape",
    "Delta",
)

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

URL_RE = re.compile(r"https?://[^\s\"'<>)\\]+")
NUMBER_RE = re.compile(r"\d")
ARTEFACT_TERMS = ("case study", "reference", "report pack", "audit", "architecture", "deck")

CTA_MARKERS = (
    "book a",
    "get in touch",
    "talk to us",
    "request a",
    "contact us",
    "see how",
    "read the",
    "download the",
    "canvasintelligence.com",
)


@lru_cache(maxsize=1)
def _load_permission_check():
    """Import the sibling permission_check module without relying on sys.path.

    Keyed on the full resolved path, so loading this package and a fixture
    copy of it in one process cannot collide. Registered in sys.modules
    because permission_check defines a dataclass, and dataclasses resolve a
    class's module through sys.modules.
    """
    module_path = (Path(__file__).resolve().parent / "permission_check.py").resolve()
    digest = hashlib.sha256(str(module_path).encode("utf-8")).hexdigest()[:16]
    module_name = f"cmos_permission_check__{digest}"
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
    """True when `marker` appears in the prompt, ignoring wrapping and case.

    prompt.md is hand-wrapped markdown, so a rule phrase routinely spans a
    line break. Collapsing all runs of whitespace to a single space before
    matching means re-wrapping a paragraph can never silently disable a rule.
    """
    normalised = " ".join(prompt_text.split()).lower()
    return " ".join(marker.split()).lower() in normalised


def mock_completion(task: dict, prompt_text: str) -> str:
    """Simulate this function's verdict for a golden task, given its prompt."""
    task_input = task.get("input", {})
    draft = str(task_input.get("draft_text", ""))
    declared_refs = list(task_input.get("client_references") or [])
    # F-BRIEF-CTA-UTM-EXEMPT (4 Aug 2026, heartbeat round 18, Pieter's
    # ruling: "Go with a for daily briefs"): the internal-brief channel is
    # never published externally, so checks 4 and 5 don't apply to it --
    # see prompt.md and schema.json's channel description.
    channel = str(task_input.get("channel") or "linkedin")
    is_internal_brief = channel == "internal-brief"

    violations: list[str] = []
    notes: list[str] = []

    if _prompt_requires(prompt_text, "permission-register.yaml"):
        permission_check = _load_permission_check()
        candidates = list(declared_refs)
        for token in KNOWN_CLIENT_TOKENS:
            if token in draft and token not in candidates:
                candidates.append(token)
        seen: set[str] = set()
        for name in candidates:
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            clearance = permission_check.check_clearance(name)
            if not clearance.allowed:
                violations.append(VIOLATION_UNCLEARED)
                notes.append(f"{clearance.status} client reference {name!r}: {clearance.reason}")

    if _prompt_requires(prompt_text, "link shortener"):
        for shortener in LINK_SHORTENERS:
            if shortener in draft:
                violations.append(VIOLATION_SHORTENER)
                notes.append(f"shortened link {shortener!r} hides its destination")

    if _prompt_requires(prompt_text, "South African English"):
        lowered = draft.lower()
        for word in US_SPELLINGS:
            if re.search(rf"\b{re.escape(word)}\b", lowered):
                violations.append(VIOLATION_SPELLING)
                notes.append(f"US spelling {word!r} — use the South African variant")

    if _prompt_requires(prompt_text, "missing-cta") and not is_internal_brief:
        lowered = draft.lower()
        if not any(marker in lowered for marker in CTA_MARKERS):
            violations.append(VIOLATION_MISSING_CTA)
            notes.append("no call to action found in the draft")

    if _prompt_requires(prompt_text, "url-utm") and not is_internal_brief:
        for url in URL_RE.findall(draft):
            if any(shortener in url for shortener in LINK_SHORTENERS):
                continue
            compliant = url.startswith("https://www.canvasintelligence.com/") and all(
                param in url for param in ("utm_source=", "utm_medium=", "utm_campaign=")
            )
            if not compliant:
                violations.append(VIOLATION_URL_UTM)
                notes.append(f"URL {url!r} is not a fully qualified UTM-tagged canvas link")

    if _prompt_requires(prompt_text, "unsupported-claim"):
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", draft):
            lowered = sentence.lower()
            if not any(term in lowered for term in UNSUPPORTED_CLAIM_TERMS):
                continue
            has_number = bool(NUMBER_RE.search(sentence))
            has_artefact = any(term in lowered for term in ARTEFACT_TERMS)
            has_client = any(token in sentence for token in KNOWN_CLIENT_TOKENS)
            if not (has_number or has_artefact or has_client):
                violations.append(VIOLATION_UNSUPPORTED)
                notes.append(f"claim carries no client, number or artefact: {sentence.strip()!r}")

    ordered = sorted(set(violations))
    return json.dumps(
        {
            "pass": not ordered,
            "violations": ordered,
            "notes": "\n".join(notes),
        },
        indent=2,
        sort_keys=True,
    )


def _parse(output: str) -> dict:
    return json.loads(output)


def run_check(task: dict, entry: dict, output: str) -> tuple[bool, str]:
    """Package-specific rubric checks. Returns (passed, detail)."""
    check = entry.get("check") or {}
    kind = check.get("kind")

    try:
        verdict = _parse(output)
    except json.JSONDecodeError as exc:
        return False, f"output is not valid JSON ({exc})"

    if kind == "verdict_pass_matches_expected":
        expected = task.get("verdict", {}).get("pass")
        actual = verdict.get("pass")
        return actual == expected, f"verdict pass is {actual!r}, expected {expected!r}"

    if kind == "verdict_violations_include_expected":
        expected = list(task.get("verdict", {}).get("violations") or [])
        actual = list(verdict.get("violations") or [])
        missing = [code for code in expected if code not in actual]
        return not missing, (
            f"verdict violations {actual} include all expected {expected}"
            if not missing
            else f"verdict violations {actual} are missing expected code(s) {missing}"
        )

    if kind == "verdict_violations_exactly":
        expected = sorted(task.get("verdict", {}).get("violations") or [])
        actual = sorted(verdict.get("violations") or [])
        return actual == expected, f"verdict violations are {actual}, expected exactly {expected}"

    if kind == "notes_name_offending_reference":
        expected_name = str(check.get("name", ""))
        notes = str(verdict.get("notes", ""))
        return (
            expected_name in notes and "UNCLEARED" in notes.upper(),
            f"notes must name {expected_name!r} and state it is uncleared; notes were {notes!r}",
        )

    if kind == "pass_is_true_only_when_no_violations":
        actual_pass = verdict.get("pass")
        actual_violations = list(verdict.get("violations") or [])
        consistent = actual_pass == (not actual_violations)
        return consistent, (
            f"pass={actual_pass!r} is consistent with violations={actual_violations}"
            if consistent
            else (
                f"pass={actual_pass!r} contradicts violations={actual_violations} (no partial pass)"
            )
        )

    return False, f"unknown check kind for {FUNCTION_ID}: {kind!r}"
