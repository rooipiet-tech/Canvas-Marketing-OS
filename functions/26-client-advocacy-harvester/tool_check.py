"""Deterministic mock + package-specific rubric checks for function 26.

The simulated decision is produced by actually running this package's own
`permission_check.check_clearance` against `docs/permission-register.yaml`,
combined with a compact, deterministic reading of the LOCAL
consent-register fixture supplied in each eval task's own input (never a
live Vault call — no such API exists). Every eval task encodes its
consent fixture row directly under `task["input"]["consent_record"]`, with
the same field names as `contracts/vault-schema/schema.sql`'s
`consent_register` table: `data_subject_ref`, `channel`, `purpose`,
`consented_at`, `revoked_at`.

`mock_completion` is prompt-derived, mirroring function 42's pattern: each
rule runs only if the package's own `prompt.md` still instructs it.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from functools import lru_cache
from pathlib import Path

FUNCTION_ID = "26-client-advocacy-harvester"

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

REDACTED_CLIENT_TEXT = "REDACTED (client naming blocked pending clearance)"
GENERIC_CLIENT_PHRASE = "a valued client"


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


def _redact_client_name(text: str, client_reference: str) -> str:
    """Replace every literal occurrence of `client_reference` with a generic phrase."""
    if not text or not client_reference:
        return text
    return re.sub(re.escape(client_reference), GENERIC_CLIENT_PHRASE, text)


def mock_completion(task: dict, prompt_text: str) -> str:
    """Simulate this function's decision for a golden task, given its prompt."""
    task_input = task.get("input", {})
    client_reference = str(task_input.get("client_reference", ""))
    proposed_quote = str(task_input.get("proposed_quote", ""))
    channel = task_input.get("channel", "linkedin")
    purpose = task_input.get("purpose", "marketing_testimonial")
    pillar = task_input.get("pillar")
    consent_record = task_input.get("consent_record") or {}

    data_subject_ref = consent_record.get("data_subject_ref")
    consent_channel = consent_record.get("channel")
    consent_purpose = consent_record.get("purpose")
    consented_at = consent_record.get("consented_at")
    revoked_at = consent_record.get("revoked_at")

    violations: list[str] = []
    reasons: list[str] = []

    if revoked_at:
        consent_status = "revoked"
        violations.append("consent-revoked")
        reasons.append(
            f"revoked_at ({revoked_at}) is set for data_subject_ref {data_subject_ref!r} "
            f"({consent_channel}/{consent_purpose}) despite an earlier consented_at "
            f"({consented_at}); a revoked_at value blocks identically to no consent at all."
        )
    elif consented_at:
        consent_status = "active"
    else:
        consent_status = "no-consent-on-file"
        violations.append("consent-missing")
        reasons.append("no consented_at is on file for this data subject/channel/purpose.")

    consent_active = consent_status == "active"

    permission_check = _load_permission_check()
    clearance = permission_check.check_clearance(client_reference)

    quote_names_client = bool(client_reference) and client_reference in proposed_quote
    if quote_names_client and not clearance.allowed:
        violations.append("uncleared-client-reference")
        # Deliberately does NOT interpolate client_reference or the raw
        # clearance.reason text here: clearance.reason is sourced from
        # docs/permission-register.yaml's free-text `reason` field, which for
        # some entries (e.g. Imperial, Rotork) itself names a DIFFERENT
        # UNCLEARED client in passing ("the Delta reference pack"). Embedding
        # either verbatim into this function's own output would leak a gated
        # name back out through the "reason" field even though the
        # harvested_record itself correctly redacts it.
        reasons.append(
            f"the quoted reference is {clearance.status} in docs/permission-register.yaml "
            "(no written permission on file); the client identity has been redacted "
            "from this harvested record."
        )

    client_named = clearance.allowed and consent_active

    if client_named:
        naming_decision = "named"
        record_quote = proposed_quote
        record_client_ref = client_reference
    elif "uncleared-client-reference" in violations:
        naming_decision = "blocked-uncleared"
        record_quote = _redact_client_name(proposed_quote, client_reference) if consent_active else None
        record_client_ref = REDACTED_CLIENT_TEXT
    elif not consent_active:
        naming_decision = "blocked-no-consent"
        record_quote = None
        record_client_ref = REDACTED_CLIENT_TEXT
    else:
        naming_decision = "written-client-free"
        record_quote = proposed_quote
        record_client_ref = REDACTED_CLIENT_TEXT

    harvested_record: dict = {
        "quote": record_quote,
        "client_reference": record_client_ref,
        "channel": channel,
        "purpose": purpose,
    }
    if pillar in PILLARS:
        harvested_record["suggested_pillar"] = pillar

    ordered_violations = sorted(set(violations))
    passed = not ordered_violations

    return json.dumps(
        {
            "client_named": client_named,
            "consent_status": consent_status,
            "harvested_record": harvested_record,
            "naming_decision": naming_decision,
            "pass": passed,
            "violations": ordered_violations,
            "reason": " ".join(reasons) if reasons else "No violations.",
        },
        indent=2,
        sort_keys=True,
    )


def run_check(task: dict, entry: dict, output: str) -> tuple[bool, str]:
    """Package-specific rubric checks. Returns (passed, detail)."""
    check = entry.get("check") or {}
    kind = check.get("kind")

    try:
        parsed = json.loads(output)
    except json.JSONDecodeError as exc:
        return False, f"output is not valid JSON ({exc})"

    if kind == "no_gated_client_named":
        blob = json.dumps(parsed)
        found = [name for name in GATED_CLIENT_NAMES if name in blob]
        return not found, (
            "no gated client name appears in the output"
            if not found
            else f"output names client(s) that are UNCLEARED in the permission register: {found}"
        )

    if kind == "consent_not_revoked":
        consent_record = task.get("input", {}).get("consent_record") or {}
        revoked_at = consent_record.get("revoked_at")
        actual_status = parsed.get("consent_status")
        if not revoked_at:
            return True, "no revoked_at present in the consent fixture; nothing to enforce here"
        return (
            actual_status == "revoked",
            f"output consent_status equals {actual_status!r}, expected it to equal "
            f"'revoked' because revoked_at={revoked_at!r} is set on the consent fixture",
        )

    if kind == "verdict_pass_matches_expected":
        expected = task.get("verdict", {}).get("pass")
        actual = parsed.get("pass")
        return actual == expected, f"output pass field is {actual!r}, expected {expected!r}"

    if kind == "verdict_violations_include_expected":
        expected = list(task.get("verdict", {}).get("violations") or [])
        actual = list(parsed.get("violations") or [])
        missing = [code for code in expected if code not in actual]
        return not missing, (
            f"output violations {actual} include all expected {expected}"
            if not missing
            else f"output violations {actual} are missing expected code(s) {missing}"
        )

    return False, f"unknown check kind for {FUNCTION_ID}: {kind!r}"
