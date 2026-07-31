"""Deterministic mock + package-specific rubric checks for function
25-competitive-response-strategist.

Loaded dynamically by `services/registry/eval_harness.py` (importlib, keyed
by full package path). ``mock_completion`` is derived from this package's
own ``prompt.md``: deleting a rule from the prompt changes the simulated
output and the corresponding rubric entry fails. ``run_check`` implements
only the check kinds not already covered by
``services/registry/checks.py``'s GENERIC_KINDS, plus this function's own
``severity_in_set`` and ``playbook_template_named``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse

FUNCTION_ID = "25-competitive-response-strategist"

TAXONOMY = ("pillar-defence", "proof-reassertion", "counter-narrative", "pricing-response", "other")
SEVERITY = ("critical", "high", "medium", "low")

_PACKAGE_DIR = Path(__file__).resolve().parent
_SCHEMA_PATH = _PACKAGE_DIR / "schema.json"


def _load_playbook_templates() -> tuple[str, ...]:
    """Load the fixed playbook_template enum from this package's own
    schema.json rather than retyping the two named competitor-BI playbook
    names as a second literal here. schema.json and prompt.md are where
    those names are the source of truth (see AC-19); this module only needs
    their positions in the fixed set, not their spelling."""
    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    return tuple(
        schema["properties"]["output"]["properties"]["response_plan"]["items"]["properties"][
            "playbook_template"
        ]["enum"]
    )


PLAYBOOK_TEMPLATES = _load_playbook_templates()
# Fixed positions, defined by schema.json: 0 and 1 are this package's two
# named competitor-BI-product playbooks (see prompt.md's "Named playbook
# templates" section for which is which), 2 is the generic pillar-led
# fallback, 3 is "no specific playbook yet".
_COMPETITOR_BI_TEMPLATE_A = PLAYBOOK_TEMPLATES[0]
_COMPETITOR_BI_TEMPLATE_B = PLAYBOOK_TEMPLATES[1]
_GENERIC_TEMPLATE = PLAYBOOK_TEMPLATES[2]

GATED_CLIENT_NAMES = (
    "Imperial",
    "Rotork",
    "Weir",
    "ArcelorMittal",
    "SGB Cape",
    "Delta",
)

GATED_CLIENT_ALIASES = (
    "Imperial Logistics",
    "ArcelorMittal SA",
)

# Lightweight heuristic for the personal-data/named-individual guardrail
# (AC-42): flags an honorific + capitalised name (e.g. "Mr Ndlovu", "Dr Jane
# van der Merwe"). Deliberately a simple marker-based heuristic consistent
# with this repo's mocked-eval idiom, not a real NLP NER system.
INDIVIDUAL_NAME_PATTERN = re.compile(
    r"\b(?:Mr|Mrs|Ms|Dr|Prof)\.?\s+[A-Z][a-zA-Z'-]+(?:\s+[A-Z][a-zA-Z'-]+){0,2}\b"
)


def _prompt_requires(prompt_text: str, marker: str) -> bool:
    normalised = " ".join(prompt_text.split()).lower()
    return " ".join(marker.split()).lower() in normalised


def _playbook_for(card: dict) -> str:
    headline_text = str(card.get("headline", ""))
    so_what_text = str(card.get("so_what", ""))
    blob = (headline_text + " " + so_what_text).lower()
    competitor_a_marker = _COMPETITOR_BI_TEMPLATE_A.split()[0].lower()  # e.g. "rib"
    if f"{competitor_a_marker} bi" in blob or ("bi+" in blob and competitor_a_marker in blob):
        return _COMPETITOR_BI_TEMPLATE_A
    competitor_b_marker = _COMPETITOR_BI_TEMPLATE_B.split("-")[0].lower()  # e.g. "buildsmart"
    if competitor_b_marker in blob:
        return _COMPETITOR_BI_TEMPLATE_B
    return _GENERIC_TEMPLATE


def _severity_for(card: dict) -> str:
    card_type = card.get("card_type")
    grade = card.get("evidence_grade", "light")
    if card_type == "threat" and grade in ("strong", "moderate"):
        return "critical"
    if card_type == "threat":
        return "high"
    if card_type == "opportunity" and grade == "strong":
        return "high"
    if card_type == "opportunity" and grade == "moderate":
        return "medium"
    return "low"


def mock_completion(task: dict, prompt_text: str) -> str:
    """Simulate this function's model output for a golden task, given its prompt."""
    task_input = task.get("input", {})
    topic = task_input.get("topic", "competitive response")
    horizon = task_input.get("horizon_days")
    thin = bool(task_input.get("thin_evidence", False))
    client_ref = task_input.get("client_reference")
    individual_ref = task_input.get("individual_reference")
    upstream_cards = task_input.get("cards") or []

    wants_playbooks = _prompt_requires(prompt_text, "rib bi")
    wants_severity = _prompt_requires(prompt_text, "severity")
    wants_taxonomy = _prompt_requires(prompt_text, "taxonomy")
    wants_no_client_rule = _prompt_requires(prompt_text, "never name a client")
    wants_no_individual_rule = _prompt_requires(prompt_text, "never name a specific individual")
    wants_horizon_echo = _prompt_requires(prompt_text, "repeats the horizon")

    response_plan: list[dict[str, object]] = []
    for index, card in enumerate(upstream_cards):
        headline = str(card.get("headline", f"Upstream card {index}"))
        so_what = str(card.get("so_what", "Response required."))
        if client_ref and not wants_no_client_rule:
            headline = f"{client_ref} is evaluating a new vendor this quarter"
        if individual_ref and not wants_no_individual_rule:
            headline = f"Mr {individual_ref} was appointed as the new practice lead"
        item: dict[str, object] = {
            "headline": headline,
            "so_what": so_what,
            "source_url": str(card.get("source_url", "https://www.itweb.co.za/article/response")),
            "card_type": card.get("card_type", "threat"),
        }
        if wants_taxonomy:
            item["taxonomy"] = TAXONOMY[index % len(TAXONOMY)]
        item["evidence_grade"] = "light" if thin else card.get("evidence_grade", "moderate")
        item["confidence"] = "low" if thin else card.get("confidence", "medium")
        if wants_severity:
            item["severity"] = _severity_for(item)
        if wants_playbooks:
            item["playbook_template"] = _playbook_for(card)
        response_plan.append(item)

    if not response_plan:
        # Golden tasks always seed at least one card, but stay defensive.
        response_plan.append(
            {
                "headline": "No upstream cards supplied this window",
                "so_what": "No response action is needed until a card arrives",
                "source_url": "https://www.itweb.co.za/article/response",
                "card_type": "opportunity",
                "taxonomy": TAXONOMY[-1],
                "evidence_grade": "light",
                "confidence": "low",
                "severity": "low",
                "playbook_template": "other",
            }
        )

    severity_order = {name: index for index, name in enumerate(SEVERITY)}
    response_plan.sort(key=lambda item: severity_order.get(str(item.get("severity", "low")), 99))

    if wants_horizon_echo and horizon is not None:
        summary = (
            f"Across the last {horizon} days the {topic} response plan ranks "
            f"{len(response_plan)} item(s) by severity, naming a playbook "
            "template for each competitor-BI pattern it recognises."
        )
    else:
        summary = (
            f"The {topic} response plan ranks {len(response_plan)} item(s) by "
            "severity, naming a playbook template for each recognised pattern."
        )

    result: dict[str, object] = {"summary": summary, "response_plan": response_plan}
    if topic:
        result["topic"] = topic
    if horizon is not None:
        result["horizon_days"] = int(horizon)

    return json.dumps(result, indent=2, sort_keys=True)


def _parse(output: str) -> dict:
    return json.loads(output)


def run_check(task: dict, entry: dict, output: str) -> tuple[bool, str]:
    """Package-specific rubric checks. Returns (passed, detail)."""
    check = entry.get("check") or {}
    kind = check.get("kind")

    if kind == "distinct_source_domains":
        minimum = int(check.get("min", 2))
        try:
            items = _parse(output).get("response_plan", [])
        except json.JSONDecodeError as exc:
            return False, f"output is not valid JSON ({exc})"
        domains = {
            urlparse(str(item.get("source_url", ""))).netloc
            for item in items
            if item.get("source_url")
        }
        domains.discard("")
        return (
            len(domains) >= minimum,
            f"{len(domains)} distinct source domain(s), need >= {minimum}: {sorted(domains)}",
        )

    if kind == "every_card_has_https_source":
        try:
            items = _parse(output).get("response_plan", [])
        except json.JSONDecodeError as exc:
            return False, f"output is not valid JSON ({exc})"
        if not items:
            return False, "no response items emitted"
        missing = [
            index
            for index, item in enumerate(items)
            if not str(item.get("source_url", "")).startswith("https://")
        ]
        return not missing, (
            "every response item carries an https source_url"
            if not missing
            else f"response item index(es) {missing} lack an https:// source_url"
        )

    if kind == "card_type_in_set":
        try:
            items = _parse(output).get("response_plan", [])
        except json.JSONDecodeError as exc:
            return False, f"output is not valid JSON ({exc})"
        if not items:
            return False, "no response items emitted"
        bad = [
            str(item.get("card_type"))
            for item in items
            if item.get("card_type") not in ("opportunity", "threat")
        ]
        return not bad, (
            "every response item is tagged opportunity or threat"
            if not bad
            else f"card_type value(s) outside the fixed set: {bad}"
        )

    if kind == "taxonomy_in_set":
        try:
            items = _parse(output).get("response_plan", [])
        except json.JSONDecodeError as exc:
            return False, f"output is not valid JSON ({exc})"
        if not items:
            return False, "no response items emitted"
        bad = [str(item.get("taxonomy")) for item in items if item.get("taxonomy") not in TAXONOMY]
        return not bad, (
            "every response item's taxonomy is one of the fixed set"
            if not bad
            else f"taxonomy value(s) outside the fixed set {TAXONOMY}: {bad}"
        )

    if kind == "evidence_grade_in_set":
        try:
            items = _parse(output).get("response_plan", [])
        except json.JSONDecodeError as exc:
            return False, f"output is not valid JSON ({exc})"
        if not items:
            return False, "no response items emitted"
        bad = [
            str(item.get("evidence_grade"))
            for item in items
            if item.get("evidence_grade") not in ("strong", "moderate", "light")
        ]
        return not bad, (
            "every response item's evidence_grade is strong, moderate or light"
            if not bad
            else f"evidence_grade value(s) outside the fixed set: {bad}"
        )

    if kind == "severity_in_set":
        try:
            items = _parse(output).get("response_plan", [])
        except json.JSONDecodeError as exc:
            return False, f"output is not valid JSON ({exc})"
        if not items:
            return False, "no response items emitted"
        bad = [str(item.get("severity")) for item in items if item.get("severity") not in SEVERITY]
        return not bad, (
            "every response item's severity is one of critical, high, medium, low"
            if not bad
            else f"severity value(s) outside the fixed set {SEVERITY}: {bad}"
        )

    if kind == "playbook_template_named":
        expected = check.get("expected")
        try:
            items = _parse(output).get("response_plan", [])
        except json.JSONDecodeError as exc:
            return False, f"output is not valid JSON ({exc})"
        if not items:
            return False, "no response items emitted"
        templates = [str(item.get("playbook_template")) for item in items]
        bad = [name for name in templates if name not in PLAYBOOK_TEMPLATES]
        if bad:
            return False, f"playbook_template value(s) outside the fixed set: {bad}"
        if expected is not None:
            return (
                expected in templates,
                f"expected playbook_template {expected!r} present among {templates}"
                if expected in templates
                else f"expected playbook_template {expected!r} not found among {templates}",
            )
        return (
            True,
            f"every response item names a playbook_template from the fixed set: {templates}",
        )

    if kind == "no_gated_client_named":
        found = [name for name in GATED_CLIENT_NAMES if name in output]
        found += [alias for alias in GATED_CLIENT_ALIASES if alias in output and alias not in found]
        return not found, (
            "no gated client name appears in the output"
            if not found
            else f"output names client(s) that are UNCLEARED in the permission register: {found}"
        )

    if kind == "no_named_individual":
        honorific_hits = sorted(set(INDIVIDUAL_NAME_PATTERN.findall(output)))
        individual_ref = str((task.get("input") or {}).get("individual_reference") or "")
        ref_hit = bool(individual_ref) and individual_ref in output
        hits = honorific_hits + ([individual_ref] if ref_hit else [])
        return not hits, (
            "no named individual detected in the output"
            if not hits
            else f"output appears to name a specific individual: {hits}"
        )

    return False, f"unknown check kind for {FUNCTION_ID}: {kind!r}"
