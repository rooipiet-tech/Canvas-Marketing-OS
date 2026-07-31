"""Deterministic mock + package-specific rubric checks for function 41.

`mock_completion` derives its behaviour from the task's own input: a signal
summary that carries no recognisable evidence marker (a digit or a known
artefact term) produces an empty `proof_points` array plus a `note` flagging
the gap, rather than a fabricated claim/source pair. This is the AC-18
"missing proof" discipline exercised by this package's evals.
"""

from __future__ import annotations

import json
import re

FUNCTION_ID = "41-research-brief-writer"

PILLARS = (
    "Finance-grade trust",
    "Consolidation at scale",
    "Fabric-native",
    "Productised speed",
    "Beyond the dashboard",
)

VERTICALS = (
    "logistics & distribution",
    "mining & industrial",
    "beverage/FMCG",
    "construction",
    "financial services",
)

# Terms that count as citable evidence in a signal summary. A digit also
# counts (checked separately) — "99.5%", "8 entities", "6 days" and so on.
EVIDENCE_MARKERS = (
    "case study",
    "reference",
    "report pack",
    "audit",
    "audited",
    "architecture",
    "deck",
    "benchmark",
    "certification",
    "partner status",
    "sla",
    "rollout log",
    "migration note",
    "audit note",
)

NUMBER_RE = re.compile(r"\d")

CFO_VOICE_NOTE = (
    "Open on the CFO's own words: finance, operations and commercial give a "
    "different number for the same question, and more than 3 days a month "
    "gets lost chasing which one is right. Read the brief in full before "
    "drafting downstream copy from it."
)

NEUTRAL_NOTE = (
    "General office-of-the-CFO audience note for a multi-entity group. Read "
    "the brief in full before drafting downstream copy from it."
)


def _prompt_requires(prompt_text: str, marker: str) -> bool:
    """True when `marker` appears in the prompt, ignoring wrapping and case."""
    normalised = " ".join(prompt_text.split()).lower()
    return " ".join(marker.split()).lower() in normalised


def mock_completion(task: dict, prompt_text: str) -> str:
    """Simulate this function's model output for a golden task, given its prompt."""
    task_input = task.get("input", {})
    pillar = task_input.get("pillar", PILLARS[0])
    vertical = task_input.get("vertical", VERTICALS[0])
    signal_summary = str(task_input.get("signal_summary", ""))

    has_evidence = bool(NUMBER_RE.search(signal_summary)) or any(
        marker in signal_summary.lower() for marker in EVIDENCE_MARKERS
    )

    brief: dict = {"pillar": pillar, "vertical": vertical, "proof_points": []}
    if has_evidence:
        brief["proof_points"] = [
            {
                "claim": signal_summary.strip(),
                "source": "signal intake note (client-free, proof over platitude)",
            }
        ]
    else:
        brief["note"] = (
            "No cited product, metric or client evidence was supplied for this "
            "signal — proof points intentionally left empty rather than "
            "fabricated."
        )

    wants_cfo_voice = _prompt_requires(prompt_text, "different number for the same question")
    audience_note = CFO_VOICE_NOTE if wants_cfo_voice else NEUTRAL_NOTE

    return json.dumps({"brief": brief, "audience_note": audience_note}, indent=2, sort_keys=True)


def run_check(task: dict, entry: dict, output: str) -> tuple[bool, str]:
    """Package-specific rubric checks. Returns (passed, detail)."""
    check = entry.get("check") or {}
    kind = check.get("kind")

    try:
        parsed = json.loads(output)
    except json.JSONDecodeError as exc:
        return False, f"output is not valid JSON ({exc})"

    if kind == "every_claim_has_source":
        proof_points = parsed.get("brief", {}).get("proof_points", [])
        missing = [point for point in proof_points if not str(point.get("source", "")).strip()]
        return not missing, (
            "every proof point carries a non-empty source"
            if not missing
            else f"{len(missing)} proof point(s) carry no source"
        )

    return False, f"unknown check kind for {FUNCTION_ID}: {kind!r}"
