"""Deterministic mock + package-specific rubric checks for function 45.

Adapted from `functions/42-linkedin-post-writer/tool_check.py`'s
prompt-derived pattern, plus a second, QA-only code path: when the task's
own input carries `csv_manifest_text`, this module validates that manifest's
shape directly (never generating a new carousel) so the golden
`csv-manifest-shape-invalid` eval exercises the real validator rather than a
hard-coded expectation.

Canva generation is fixture-first everywhere in this package: the Canva
Bulk Create CSV manifest is produced and shape-checked entirely in-process.
No live mcp-canva call exists in this module.
"""

from __future__ import annotations

import json
import re

FUNCTION_ID = "45-carousel-post-writer"

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

# The Canva Bulk Create CSV manifest header this package always emits, and
# checks any supplied manifest against. One data row per slide, no exception.
CANVA_BULK_CREATE_CSV_HEADER = "slide_number,headline,subhead,image_ref,brand_template_id"

VIOLATION_CSV_HEADER_MISMATCH = "csv-header-mismatch"
VIOLATION_CSV_ROW_COUNT_MISMATCH = "csv-row-count-mismatch"
VIOLATION_CSV_EMPTY = "csv-empty"


def _prompt_requires(prompt_text: str, marker: str) -> bool:
    """True when `marker` appears in the prompt, ignoring wrapping and case."""
    normalised = " ".join(prompt_text.split()).lower()
    return " ".join(marker.split()).lower() in normalised


def validate_canva_bulk_create_csv(
    csv_text: str, expected_rows: int | None
) -> tuple[bool, list[str], str]:
    """Validate a Canva Bulk Create CSV manifest's shape.

    Checks the fixed header row (`CANVA_BULK_CREATE_CSV_HEADER`) and, when
    `expected_rows` is supplied, that the data-row count matches it exactly.
    Never touches a live Canva API — this is a purely local shape check over
    a manifest string, used both to prove this package's own generated
    manifest is well-formed and to reject a deliberately malformed one.
    """
    lines = [line for line in (csv_text or "").splitlines() if line.strip() != ""]
    violations: list[str] = []

    if not lines:
        violations.append(VIOLATION_CSV_EMPTY)
    elif lines[0].strip() != CANVA_BULK_CREATE_CSV_HEADER:
        violations.append(VIOLATION_CSV_HEADER_MISMATCH)

    data_rows = lines[1:] if lines else []
    if expected_rows is not None and len(data_rows) != expected_rows:
        violations.append(VIOLATION_CSV_ROW_COUNT_MISMATCH)

    ok = not violations
    if ok:
        reason = (
            f"Canva Bulk Create CSV manifest header matches and {len(data_rows)} data "
            "row(s) match the expected slide count"
        )
    else:
        detail_by_code = {
            VIOLATION_CSV_EMPTY: "the manifest has no rows at all",
            VIOLATION_CSV_HEADER_MISMATCH: (
                f"header row does not equal {CANVA_BULK_CREATE_CSV_HEADER!r}"
            ),
            VIOLATION_CSV_ROW_COUNT_MISMATCH: (
                f"data row count {len(data_rows)} does not match the expected "
                f"{expected_rows} slide(s)"
            ),
        }
        reason = "Canva Bulk Create CSV manifest is malformed: " + "; ".join(
            detail_by_code[code] for code in violations
        )
    return ok, violations, reason


def _cta_url(campaign: str) -> str:
    return (
        "https://www.canvasintelligence.com/insights/carousel-proof-stack"
        "?utm_source=linkedin&utm_medium=social"
        f"&utm_campaign={campaign}"
    )


def _build_slides(proof_points: list[str], pillar: str, wants_verbatim_pillar: bool) -> list[dict]:
    slides = []
    for index, proof_point in enumerate(proof_points, start=1):
        slides.append(
            {
                "slide_number": index,
                "headline": f"Proof {index}",
                "subhead": str(proof_point),
            }
        )
    closing_subhead = (
        f"This is the {pillar} pillar in practice."
        if wants_verbatim_pillar
        else "This is what our approach looks like in practice."
    )
    slides.append(
        {
            "slide_number": len(proof_points) + 1,
            "headline": ROOF_LINE,
            "subhead": closing_subhead,
        }
    )
    return slides


FORMULA_TRIGGER_CHARS = ("=", "+", "-", "@")


def _csv_cell(value: str) -> str:
    """Escape a value for a CSV cell: neutralize commas and, defensively,
    any leading formula-trigger character (=, +, -, @) that a spreadsheet
    application would otherwise interpret as a formula."""
    text = str(value).replace(",", ";")
    if text.startswith(FORMULA_TRIGGER_CHARS):
        text = f"'{text}"
    return text


def _build_csv(slides: list[dict]) -> str:
    lines = [CANVA_BULK_CREATE_CSV_HEADER]
    for slide in slides:
        lines.append(
            ",".join(
                [
                    str(slide["slide_number"]),
                    _csv_cell(slide["headline"]),
                    _csv_cell(slide.get("subhead", "")),
                    f"slide-{slide['slide_number']}.png",
                    "carousel-standard-v1",
                ]
            )
        )
    return "\n".join(lines)


def _generate_carousel(task_input: dict, prompt_text: str) -> str:
    pillar = task_input.get("pillar", PILLARS[0])
    proof_points = list(task_input.get("proof_points") or [])
    campaign = task_input.get("campaign", "general")

    wants_verbatim_pillar = _prompt_requires(prompt_text, "use them verbatim")

    slides = _build_slides(proof_points, pillar, wants_verbatim_pillar)
    csv_text = _build_csv(slides)

    return json.dumps(
        {
            "slides": slides,
            "canva_bulk_create_csv": csv_text,
            "cta_url": _cta_url(campaign),
        },
        indent=2,
        sort_keys=True,
    )


def _qa_manifest(task_input: dict) -> str:
    csv_text = str(task_input.get("csv_manifest_text", ""))
    expected_rows = task_input.get("expected_row_count")
    ok, violations, reason = validate_canva_bulk_create_csv(csv_text, expected_rows)
    return json.dumps(
        {"pass": ok, "violations": violations, "reason": reason}, indent=2, sort_keys=True
    )


def mock_completion(task: dict, prompt_text: str) -> str:
    """Simulate this function's output for a golden task, given its prompt.

    Dispatches on the task's own input shape: `csv_manifest_text` present
    means the QA/manifest-validation path; its absence means the normal
    generative carousel-plus-manifest path.
    """
    task_input = task.get("input", {})
    if "csv_manifest_text" in task_input:
        return _qa_manifest(task_input)
    return _generate_carousel(task_input, prompt_text)


def run_check(task: dict, entry: dict, output: str) -> tuple[bool, str]:
    """Package-specific rubric checks. Returns (passed, detail)."""
    check = entry.get("check") or {}
    kind = check.get("kind")

    try:
        parsed = json.loads(output)
    except json.JSONDecodeError as exc:
        return False, f"output is not valid JSON ({exc})"

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

    if kind == "canva_csv_shape_valid":
        csv_text = parsed.get("canva_bulk_create_csv", "")
        slides = parsed.get("slides", [])
        ok, violations, reason = validate_canva_bulk_create_csv(csv_text, len(slides))
        if not ok:
            return ok, reason
        return ok, f"manifest shape valid: {reason} (violations: {violations})"

    if kind == "roof_line_final_slide":
        slides = parsed.get("slides", [])
        if not slides:
            return False, "output carries no slides at all"
        last_headline = str(slides[-1].get("headline", ""))
        return (
            last_headline == ROOF_LINE,
            f"final slide headline is {last_headline!r}, expected {ROOF_LINE!r}",
        )

    if kind == "pillar_named_verbatim":
        expected = task.get("input", {}).get("pillar")
        if expected not in PILLARS:
            return False, f"task input pillar {expected!r} is not one of the five pillar names"
        blob = json.dumps(parsed)
        return expected in blob, f"output does not contain the verbatim pillar name {expected!r}"

    if kind == "cta_utm_complete":
        url = str(parsed.get("cta_url", ""))
        if not url:
            return False, "output cta_url field is empty"
        if not url.startswith("https://www.canvasintelligence.com/"):
            return False, f"CTA URL {url!r} is not a full https://www.canvasintelligence.com/ link"
        missing = [
            param
            for param in ("utm_source=", "utm_medium=", "utm_campaign=")
            if param not in url
        ]
        if missing:
            return False, f"CTA URL is missing UTM parameter(s): {missing}"
        shortener = [name for name in LINK_SHORTENERS if name in url]
        if shortener:
            return False, f"CTA URL contains link shortener(s): {shortener}"
        return True, "cta_url is a fully qualified URL with all three UTM parameters"

    if kind == "no_gated_client_named":
        blob = json.dumps(parsed)
        found = [name for name in GATED_CLIENT_NAMES if name in blob]
        return not found, (
            "no gated client name appears in the output"
            if not found
            else f"output names client(s) that are UNCLEARED in the permission register: {found}"
        )

    return False, f"unknown check kind for {FUNCTION_ID}: {kind!r}"
