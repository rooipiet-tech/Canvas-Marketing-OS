#!/usr/bin/env python3
"""Validate function-definition packages against the frozen TEMPLATE shape.

Run standalone (from repo root):

    python services/registry/validate_package.py functions/09-market-intelligence-director
    python services/registry/validate_package.py --all
    python services/registry/validate_package.py --min-eval-tasks 1 <dir>

Rules enforced, each reported by name on failure:

  required-file-missing        prompt.md / skill.md / tools.yaml / schema.json absent
  required-dir-missing         evals/ absent
  empty-file                   a required file exists but has no content
  schema-json-invalid          schema.json is not parseable / not a valid
                               Draft 2020-12 JSON Schema
  schema-id-missing-version    schema.json $id has no /vN/ version segment
  prompt-missing-json-output-contract
                               prompt.md never instructs the model to return
                               a single JSON object (dispatch.py parses every
                               model response as JSON unconditionally)
  tools-yaml-unparseable       tools.yaml is not parseable YAML
  tools-yaml-schema-violation  tools.yaml violates
                               contracts/function-definition/tools.schema.json
  tools-yaml-duplicate-name    two tools share a name
  eval-task-count              fewer golden eval tasks than required
  eval-task-invalid            an eval task file is not parseable JSON
  eval-task-schema-violation   an eval task violates eval_task.schema.json
  eval-task-duplicate-id       two eval tasks in one package share an id

Exits 0 and prints PASS on success. Exits 1 with `FAIL: <reason>` on stderr
otherwise.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from common import (
    EVAL_TASK_SCHEMA_PATH,
    REQUIRED_PACKAGE_DIRS,
    REQUIRED_PACKAGE_FILES,
    TOOLS_SCHEMA_PATH,
    discover_function_packages,
    eval_task_files,
    fail,
    rel_or_str,
)
from jsonschema import Draft202012Validator

DEFAULT_MIN_EVAL_TASKS = 5

VERSION_SEGMENT_RE = re.compile(r"/v\d+(?:[./]|$)")


def _rule_failure(rule: str, path: Path, detail: str) -> str:
    return f"[{rule}] {rel_or_str(path)}: {detail}"


def _load_schema(path: Path, label: str) -> dict:
    if not path.is_file():
        fail(f"canonical {label} schema is missing at {rel_or_str(path)}")
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(f"canonical {label} schema {rel_or_str(path)} is not valid JSON: {exc}")
    try:
        Draft202012Validator.check_schema(schema)
    except Exception as exc:  # noqa: BLE001 - jsonschema raises several types
        fail(f"canonical {label} schema {rel_or_str(path)} is not a valid JSON Schema: {exc}")
    return schema


def _describe(error) -> str:
    location = "/".join(str(part) for part in error.absolute_path) or "<root>"
    return f"at {location}: {error.message}"


def check_required_shape(package_dir: Path, problems: list[str]) -> None:
    for name in REQUIRED_PACKAGE_FILES:
        candidate = package_dir / name
        if not candidate.is_file():
            problems.append(
                _rule_failure(
                    "required-file-missing",
                    candidate,
                    f"{name} is required by contracts/function-definition/TEMPLATE/",
                )
            )
        elif not candidate.read_text(encoding="utf-8").strip():
            problems.append(_rule_failure("empty-file", candidate, f"{name} has no content"))

    for name in REQUIRED_PACKAGE_DIRS:
        candidate = package_dir / name
        if not candidate.is_dir():
            problems.append(
                _rule_failure(
                    "required-dir-missing",
                    candidate,
                    f"{name}/ is required by contracts/function-definition/TEMPLATE/",
                )
            )


def check_schema_json(package_dir: Path, problems: list[str]) -> None:
    path = package_dir / "schema.json"
    if not path.is_file():
        return
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        problems.append(_rule_failure("schema-json-invalid", path, f"not valid JSON: {exc}"))
        return
    try:
        Draft202012Validator.check_schema(document)
    except Exception as exc:  # noqa: BLE001 - jsonschema raises several types
        problems.append(
            _rule_failure(
                "schema-json-invalid",
                path,
                f"not a valid Draft 2020-12 JSON Schema: {exc}",
            )
        )
        return
    schema_id = str(document.get("$id", ""))
    if not VERSION_SEGMENT_RE.search(schema_id):
        problems.append(
            _rule_failure(
                "schema-id-missing-version",
                path,
                f"$id {schema_id!r} has no /vN/ version segment",
            )
        )


def check_prompt_json_contract(package_dir: Path, problems: list[str]) -> None:
    """Every function's dispatch handler unconditionally parses the model's
    raw response as JSON (dispatch.py's `_parse_json_content` -- strips a
    markdown fence, then `json.loads`s; there is no plain-text fallback path
    in production). The model only knows to comply if its OWN prompt.md
    tells it to -- schema.json documents the shape but is never sent to the
    model itself, so a schema.json without a matching prompt.md instruction
    is invisible to the LLM at inference time.

    F-PROMPT-OUTPUT-CONTRACT (5 Aug 2026, heartbeat round 18c): found after
    `draft-content` (function 42) consistently failed to parse in
    production -- `functions/42-linkedin-post-writer/prompt.md` never told
    the model to return JSON at all, unlike its sibling functions 02 and 09,
    which both open with an explicit "## Output contract" / "Return a single
    JSON object and nothing else" section. A repo-wide sweep found 6 of 23
    packages missing this instruction (42 already fixed as part of this same
    change; 39, 43, 45, 46, 47, 52 fixed alongside it) -- this rule is what
    stops a 7th slipping in unnoticed, since nothing else in this validator
    (or in CI) previously checked prompt.md content at all, only its
    presence/non-emptiness (see check_required_shape's `empty-file` rule).

    Deliberately a substring match on the same phrasing every compliant
    prompt.md already uses, not a semantic/LLM-judged check -- consistent
    with this script's own house style (every other rule here is a
    mechanical, deterministic check). A prompt is free to phrase the rest of
    its output contract however it likes; this only enforces that the
    JSON-only instruction is present somewhere in the file.
    """
    path = package_dir / "prompt.md"
    if not path.is_file():
        return
    normalised = " ".join(path.read_text(encoding="utf-8").split()).lower()
    marker = "return a single json object"
    if marker not in normalised:
        problems.append(
            _rule_failure(
                "prompt-missing-json-output-contract",
                path,
                "prompt.md never instructs the model to return a single JSON "
                "object and nothing else -- dispatch.py's handlers parse every "
                "model response as JSON unconditionally, so a model given this "
                "prompt with no such instruction will very likely respond with "
                "prose/markdown that fails to parse in production (see "
                "F-PROMPT-OUTPUT-CONTRACT)",
            )
        )


def check_tools_yaml(package_dir: Path, tools_schema: dict, problems: list[str]) -> None:
    import yaml

    path = package_dir / "tools.yaml"
    if not path.is_file():
        return
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        problems.append(_rule_failure("tools-yaml-unparseable", path, f"invalid YAML: {exc}"))
        return

    errors = sorted(
        Draft202012Validator(tools_schema).iter_errors(document),
        key=lambda err: list(err.absolute_path),
    )
    for error in errors:
        problems.append(
            _rule_failure(
                "tools-yaml-schema-violation",
                path,
                f"violates contracts/function-definition/tools.schema.json {_describe(error)}",
            )
        )
    if errors:
        return

    names = [str(tool.get("name")) for tool in document.get("tools", [])]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        problems.append(
            _rule_failure(
                "tools-yaml-duplicate-name", path, f"duplicate tool name(s): {duplicates}"
            )
        )


def check_eval_tasks(
    package_dir: Path, task_schema: dict, min_tasks: int, problems: list[str]
) -> int:
    task_paths = eval_task_files(package_dir)
    if len(task_paths) < min_tasks:
        problems.append(
            _rule_failure(
                "eval-task-count",
                package_dir / "evals",
                f"{len(task_paths)} golden eval task file(s) found, at least {min_tasks} required",
            )
        )

    validator = Draft202012Validator(task_schema)
    seen_ids: dict[str, Path] = {}
    for task_path in task_paths:
        try:
            task = json.loads(task_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            problems.append(_rule_failure("eval-task-invalid", task_path, f"not valid JSON: {exc}"))
            continue
        errors = sorted(validator.iter_errors(task), key=lambda err: list(err.absolute_path))
        for error in errors:
            problems.append(
                _rule_failure(
                    "eval-task-schema-violation",
                    task_path,
                    f"violates services/registry/eval_task.schema.json {_describe(error)}",
                )
            )
        if errors:
            continue
        task_id = str(task["id"])
        if task_id in seen_ids:
            problems.append(
                _rule_failure(
                    "eval-task-duplicate-id",
                    task_path,
                    f"task id {task_id!r} already used by {rel_or_str(seen_ids[task_id])}",
                )
            )
        else:
            seen_ids[task_id] = task_path

    return len(task_paths)


def validate_package(package_dir: Path, tools_schema: dict, task_schema: dict, min_tasks: int):
    problems: list[str] = []
    if not package_dir.is_dir():
        fail(f"[package-missing] {rel_or_str(package_dir)}: not a directory")

    check_required_shape(package_dir, problems)
    check_schema_json(package_dir, problems)
    check_prompt_json_contract(package_dir, problems)
    check_tools_yaml(package_dir, tools_schema, problems)
    task_count = check_eval_tasks(package_dir, task_schema, min_tasks, problems)
    return problems, task_count


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate function-definition packages against the TEMPLATE shape."
    )
    parser.add_argument("packages", nargs="*", type=Path, help="package directories to validate")
    parser.add_argument(
        "--all",
        action="store_true",
        help="validate every function-definition package under functions/",
    )
    parser.add_argument(
        "--min-eval-tasks",
        type=int,
        default=DEFAULT_MIN_EVAL_TASKS,
        help=f"minimum golden eval tasks per package (default {DEFAULT_MIN_EVAL_TASKS})",
    )
    args = parser.parse_args()

    package_dirs = list(args.packages)
    if args.all:
        package_dirs.extend(discover_function_packages())
    if not package_dirs:
        fail("no package directory given — pass one or more paths, or --all")

    tools_schema = _load_schema(TOOLS_SCHEMA_PATH, "tools.yaml")
    task_schema = _load_schema(EVAL_TASK_SCHEMA_PATH, "eval task")

    all_problems: list[str] = []
    for package_dir in package_dirs:
        problems, task_count = validate_package(
            package_dir, tools_schema, task_schema, args.min_eval_tasks
        )
        all_problems.extend(problems)
        if not problems:
            print(f"ok  {rel_or_str(package_dir)} ({task_count} golden eval task(s))")

    if all_problems:
        fail(
            f"{len(all_problems)} package rule violation(s):\n  " + "\n  ".join(all_problems)
        )

    print(f"validated {len(package_dirs)} package(s)")
    print("PASS")


if __name__ == "__main__":
    main()
