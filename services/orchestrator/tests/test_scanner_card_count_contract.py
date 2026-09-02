"""No card-emitting scanner may demand a count its evidence cannot supply.

F-SCAN-QUIET-ZERO. The sibling of F-INGEST-QUIET-ZERO, found by looking
rather than by an incident -- and it is NOT the same bug, which is why
the fix is not the same fix.

FUNCTION 09's trap was an honesty rule the schema forbade obeying:
prompt.md hard rule 9 said "never pad the batch back up to the minimum"
while schema.json demanded minItems >= 1, so a scan that truthfully found
nothing had no legal answer.

THE ELEVEN SCANNERS had the opposite shape. Their prompts said "Return
**at least 3** cards, at most 8" with NO padding rule at all, against a
schema of minItems 1 / maxItems 10. Three separate disagreements:

  * the prompt demanded 3, the schema accepted 1
  * the prompt capped at 8, the schema accepted 10
  * nothing anywhere told the model that finding little was allowed

So relaxing minItems to 0 on its own would have achieved nothing here:
the model was still under instruction to produce three, and would pad or
fail rather than report a quiet scan. The prompts had to say zero is
legal for the schema change to mean anything.

WHY IT MATTERS. deploy-pipeline run 21 dead-lettered both sourced
scanners -- competitor-discovery and fabric-ecosystem, the only two given
real source URLs on 2 Sep. The smoke still passed, because the eleven
scanners are not in the polled lineage.

The root cause of THOSE two dead-letters is NOT confirmed: it lives in
ca-orchestrator's logs, and the dead-letter alert contract carries
task_id/task_type/loop_id/failure_count/dead_lettered_at and no error
string. This contract fix is correct on its own terms whether or not it
was the cause -- but it is not evidence about run 21, and must not be
cited as such.

Unlike function 09, no downstream stand-down is needed:
dedupe_signal_cards_handler already tolerates a zero-card batch (it
reports cards_out=0 and merges on), whereas score_signals_handler raised.
Checked, not assumed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

FUNCTIONS = Path(__file__).resolve().parents[3] / "functions"


def _card_emitting() -> list[Path]:
    """Every function package whose output schema declares `cards`."""
    out = []
    for schema_path in sorted(FUNCTIONS.glob("*/schema.json")):
        schema = json.loads(schema_path.read_text())
        props = schema.get("properties", {}).get("output", {}).get("properties", {})
        if "cards" in props:
            out.append(schema_path.parent)
    return out


def test_the_scanner_set_is_not_empty():
    """Guard the guard: an empty list makes every test below vacuous.

    The lesson from the review on #145 -- a check that cannot fail is
    worse than no check, because it reads as coverage.
    """
    packages = _card_emitting()
    assert len(packages) >= 11, f"found only {len(packages)} card-emitting function(s)"


@pytest.mark.parametrize("package", _card_emitting(), ids=lambda p: p.name)
def test_the_schema_permits_a_scan_that_found_nothing(package: Path):
    cards = json.loads((package / "schema.json").read_text())["properties"]["output"][
        "properties"
    ]["cards"]

    assert cards["minItems"] == 0, (
        f"{package.name}: minItems {cards['minItems']} makes an honest empty scan a "
        "schema violation, which dead-letters the task and cascades to every "
        "descendant. A floor above 0 leaves a scan that genuinely found nothing no "
        "legal answer."
    )
    # The ceiling is a real editorial limit and stays.
    assert cards["maxItems"] >= 1


@pytest.mark.parametrize("package", _card_emitting(), ids=lambda p: p.name)
def test_the_prompt_says_zero_is_allowed(package: Path):
    """The half that makes the schema change mean anything.

    minItems 0 with a prompt still demanding three only moves the
    disagreement; the model obeys the prompt, not the schema.
    """
    prompt = (package / "prompt.md").read_text()

    assert "at least 3" not in prompt, (
        f"{package.name}: the prompt still demands 'at least 3' cards. With no "
        "padding rule to defer to, that instruction is what makes a truthful short "
        "scan impossible -- the schema floor was only half of it."
    )
    assert "Zero is also a correct answer" in prompt, (
        f"{package.name}: the prompt never tells the model that finding nothing is "
        "an acceptable outcome. Leaving it merely unforbidden is not enough: the "
        "model has to be told, or it pads."
    )
    assert "3 to 8 cards on an ordinary day" in prompt, (
        f"{package.name}: the ordinary-day guidance is gone. Without it the prompt "
        "gives no target at all, which is a different failure from demanding one."
    )


def _minimal_valid_payload(output_schema: dict) -> dict:
    """A payload satisfying every required field EXCEPT carrying no cards.

    Built from the schema rather than hardcoded: the six vertical
    scanners require a `vertical` field the others do not, and a
    hand-written fixture missing it fails for a reason that has nothing
    to do with the empty array under test. (It did, first time.)
    """
    props = output_schema.get("properties", {})
    payload: dict = {}
    for field in output_schema.get("required", []):
        spec = props.get(field, {})
        if field == "cards":
            payload[field] = []
        elif "const" in spec:
            # The six vertical scanners pin `vertical` with const, not enum.
            payload[field] = spec["const"]
        elif "enum" in spec:
            payload[field] = spec["enum"][0]
        elif spec.get("type") == "integer":
            payload[field] = max(int(spec.get("minimum", 1)), 1)
        elif spec.get("type") == "array":
            payload[field] = []
        else:
            # Long enough to clear any minLength a summary-like field sets.
            payload[field] = "x" * max(int(spec.get("minLength", 1)), 80)
    return payload


@pytest.mark.parametrize("package", _card_emitting(), ids=lambda p: p.name)
def test_a_zero_card_scan_actually_validates(package: Path):
    """The proof that matters, run through the real validator.

    `minItems == 0` read out of the file is a claim ABOUT the schema;
    this is the schema answering. dispatch._validate_function_output uses
    Draft202012Validator against this same document, so a payload that
    passes here is one that will not dead-letter the task.
    """
    schema = json.loads((package / "schema.json").read_text())["properties"]["output"]
    quiet_scan = _minimal_valid_payload(schema)
    assert quiet_scan["cards"] == [], "the fixture must be testing an EMPTY batch"

    errors = sorted(
        Draft202012Validator(schema).iter_errors(quiet_scan),
        key=lambda err: list(err.absolute_path),
    )

    assert not errors, (
        f"{package.name}: an honest zero-card scan is still a schema violation "
        f"({len(errors)} error(s), first: {errors[0].message[:120] if errors else ''}). "
        "That dead-letters the task after three retries and cascades to every "
        "descendant."
    )
