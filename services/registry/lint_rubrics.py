#!/usr/bin/env python3
"""Lint golden eval rubrics for gradeability.

Run standalone (from repo root):

    python services/registry/lint_rubrics.py functions/42-linkedin-post-writer/evals
    python services/registry/lint_rubrics.py --all

The bar this enforces is AC-18: every rubric entry must be a discrete,
checkable pass/fail condition that two independent graders decide
identically. The failure mode it exists to prevent is a rubric that reads
well and grades badly — "the post is compelling", "tone is appropriate" —
because such an entry silently converts an automated gate into a coin toss.

Rules, each reported by name with a file and line number:

  rubric-empty            a task has an empty rubric array
  rubric-missing-field    an entry lacks criterion, condition or check_type
  rubric-bad-check-type   check_type is not tool_check or llm_judged
  rubric-condition-short  the condition is too short to be a real test
  rubric-subjective-term  an unqualified subjective term with no threshold
  rubric-no-anchor        the condition names no concrete, observable anchor
  rubric-check-missing    the entry has no machine-executable `check` block

Exits 0 and prints PASS when clean; exits 1 with `FAIL: <reason>` otherwise.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from common import discover_function_packages, fail, rel_or_str

MIN_CONDITION_LENGTH = 20

VALID_CHECK_TYPES = ("tool_check", "llm_judged")

# Terms that describe a grader's impression rather than an observable fact.
# Banned unless the same condition states a threshold that makes them
# decidable.
SUBJECTIVE_TERMS = (
    "good",
    "great",
    "compelling",
    "appropriate",
    "appropriately",
    "well-written",
    "well written",
    "engaging",
    "nice",
    "strong",
    "powerful",
    "effective",
    "professional",
    "high-quality",
    "high quality",
    "suitable",
    "reasonable",
    "natural",
    "polished",
    "punchy",
    "on-brand",
    "impactful",
    "persuasive",
    "readable",
    "flows well",
    "sounds right",
    "feels",
)

# A threshold or quantifier that makes an otherwise subjective term decidable.
THRESHOLD_MARKERS = (
    "at least",
    "at most",
    "no more than",
    "no fewer than",
    "exactly",
    "between",
    "<=",
    ">=",
    "%",
    "score of",
    "rated",
    "on a scale",
)

# A concrete, observable anchor: what the grader actually looks at.
ANCHOR_MARKERS = (
    "contains",
    "does not contain",
    "zero occurrences",
    "exact",
    "exactly",
    "literal",
    "starts with",
    "ends with",
    "equal",
    "equals",
    "matches",
    "parses",
    "array",
    "field",
    "at least",
    "at most",
    "between",
    "is true",
    "is false",
    "empty list",
    "set of",
    "one of",
    "%",
)

DIGIT_RE = re.compile(r"\d")


class Violation:
    def __init__(self, path: Path, line: int, rule: str, detail: str) -> None:
        self.path = path
        self.line = line
        self.rule = rule
        self.detail = detail

    def render(self) -> str:
        return f"{rel_or_str(self.path)}:{self.line}: {self.rule}: {self.detail}"


def find_line(lines: list[str], needle: str, fallback: int = 1) -> int:
    """Line number of the first line containing a distinctive fragment."""
    fragment = (needle or "").strip()[:60]
    if not fragment:
        return fallback
    for number, line in enumerate(lines, start=1):
        if fragment in line:
            return number
    return fallback


def has_marker(text: str, markers: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in markers)


def lint_entry(
    path: Path, lines: list[str], task_id: str, index: int, entry: object
) -> list[Violation]:
    violations: list[Violation] = []
    if not isinstance(entry, dict):
        return [
            Violation(
                path, 1, "rubric-missing-field", f"{task_id} rubric[{index}] is not an object"
            )
        ]

    criterion = str(entry.get("criterion", ""))
    condition = str(entry.get("condition", ""))
    check_type = str(entry.get("check_type", ""))
    line = find_line(lines, condition or criterion)
    label = f"{task_id} rubric[{index}]"

    for field, value in (("criterion", criterion), ("condition", condition)):
        if not value.strip():
            violations.append(
                Violation(path, line, "rubric-missing-field", f"{label} has no {field}")
            )

    if check_type not in VALID_CHECK_TYPES:
        violations.append(
            Violation(
                path,
                line,
                "rubric-bad-check-type",
                f"{label} check_type {check_type!r} is not one of {list(VALID_CHECK_TYPES)}",
            )
        )

    if condition and len(condition.strip()) < MIN_CONDITION_LENGTH:
        violations.append(
            Violation(
                path,
                line,
                "rubric-condition-short",
                f"{label} condition is {len(condition.strip())} characters — too short to "
                f"state a discrete test (minimum {MIN_CONDITION_LENGTH})",
            )
        )

    combined = f"{criterion} {condition}"
    has_threshold = has_marker(condition, THRESHOLD_MARKERS) or bool(DIGIT_RE.search(condition))
    for term in SUBJECTIVE_TERMS:
        if not re.search(rf"(?<![a-z]){re.escape(term)}(?![a-z])", combined.lower()):
            continue
        if has_threshold:
            continue
        violations.append(
            Violation(
                path,
                line,
                "rubric-subjective-term",
                f"{label} uses the subjective term {term!r} with no threshold or quantifier — "
                "two graders can disagree, so this is not a pass/fail condition",
            )
        )

    if condition and not has_marker(condition, ANCHOR_MARKERS) and not DIGIT_RE.search(condition):
        violations.append(
            Violation(
                path,
                line,
                "rubric-no-anchor",
                f"{label} condition names no observable anchor (an exact string, a count, a "
                "field, a comparison) — state what a grader looks at",
            )
        )

    if not isinstance(entry.get("check"), dict) or not entry.get("check", {}).get("kind"):
        violations.append(
            Violation(
                path,
                line,
                "rubric-check-missing",
                f"{label} has no `check` block with a `kind` — without one the condition "
                "cannot be executed identically on every run",
            )
        )

    return violations


def lint_task_file(path: Path) -> list[Violation]:
    raw = path.read_text(encoding="utf-8")
    lines = raw.splitlines()
    try:
        task = json.loads(raw)
    except json.JSONDecodeError as exc:
        return [Violation(path, 1, "rubric-missing-field", f"not valid JSON: {exc}")]

    task_id = str(task.get("id", path.stem))
    rubric = task.get("rubric")
    if not isinstance(rubric, list) or not rubric:
        return [
            Violation(
                path,
                find_line(lines, '"rubric"'),
                "rubric-empty",
                f"{task_id} has an empty or missing rubric array — a task with no rubric "
                "cannot pass or fail",
            )
        ]

    violations: list[Violation] = []
    for index, entry in enumerate(rubric):
        violations.extend(lint_entry(path, lines, task_id, index, entry))
    return violations


def collect_task_files(targets: list[Path]) -> list[Path]:
    files: list[Path] = []
    for target in targets:
        if target.is_file():
            files.append(target)
        elif target.is_dir():
            evals_dir = target / "evals" if (target / "evals").is_dir() else target
            files.extend(sorted(evals_dir.rglob("*.json")))
        else:
            fail(f"{rel_or_str(target)} is neither a file nor a directory")
    return files


def main() -> None:
    parser = argparse.ArgumentParser(description="Lint golden eval rubrics for gradeability.")
    parser.add_argument("targets", nargs="*", type=Path, help="eval task files or directories")
    parser.add_argument(
        "--all",
        action="store_true",
        help="lint the shipped evals/ of every function package under functions/",
    )
    args = parser.parse_args()

    targets = list(args.targets)
    if args.all:
        targets.extend(discover_function_packages())
    if not targets:
        fail("nothing to lint - pass eval task files/directories, or --all")

    task_files = collect_task_files(targets)
    if not task_files:
        fail("no eval task JSON files found in the given target(s)")

    violations: list[Violation] = []
    entry_count = 0
    for task_file in task_files:
        violations.extend(lint_task_file(task_file))
        try:
            entry_count += len(json.loads(task_file.read_text(encoding="utf-8")).get("rubric", []))
        except (json.JSONDecodeError, AttributeError):
            pass

    for violation in sorted(violations, key=lambda v: (str(v.path), v.line, v.rule)):
        print(violation.render(), file=sys.stderr)

    if violations:
        rules = sorted({violation.rule for violation in violations})
        fail(
            f"{len(violations)} rubric violation(s) across {len(task_files)} task file(s); "
            f"rules: {', '.join(rules)}"
        )

    print(f"linted {entry_count} rubric entrie(s) across {len(task_files)} task file(s)")
    print("PASS")


if __name__ == "__main__":
    main()
