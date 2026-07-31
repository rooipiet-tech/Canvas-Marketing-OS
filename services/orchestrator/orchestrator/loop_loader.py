"""Load, schema-validate, and acyclicity-check orchestrator loop YAML files.

Used by:
  - scripts/validate_loops.py (repo root, imports this module via a
    sys.path insertion — see that file's header)
  - services/orchestrator/scripts/decompose_cli.py
  - orchestrator/main.py's FastAPI lifespan (loads both shipped loops on
    startup)
  - services/orchestrator/tests/test_loop_schema_acyclic.py
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

from orchestrator import config
from orchestrator.models import LoopDefinition


class CyclicLoopError(Exception):
    """Raised when a loop definition's depends_on edges contain a cycle."""


class LoopValidationError(Exception):
    """Raised when a loop definition fails JSON Schema or referential validation."""


def _schema_path() -> Path:
    return config.contracts_dir() / "orchestrator" / "loop-definition.schema.json"


def _load_schema() -> dict[str, Any]:
    return json.loads(_schema_path().read_text(encoding="utf-8"))


def load_loop(path: str | Path) -> LoopDefinition:
    """Parse a loop YAML file, validate it against the loop-definition
    schema, and return a LoopDefinition. Raises LoopValidationError on any
    schema or referential-integrity failure.
    """
    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))

    schema = _load_schema()
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(raw), key=lambda e: e.path)
    if errors:
        messages = "; ".join(f"{list(e.path)}: {e.message}" for e in errors)
        raise LoopValidationError(f"{path}: schema validation failed: {messages}")

    loop = LoopDefinition.model_validate(raw)

    task_ids = [t.task_id for t in loop.tasks]
    if len(task_ids) != len(set(task_ids)):
        dupes = {t for t in task_ids if task_ids.count(t) > 1}
        raise LoopValidationError(f"{path}: duplicate task_id(s): {sorted(dupes)}")

    task_id_set = set(task_ids)
    for task in loop.tasks:
        for dep in task.depends_on:
            if dep not in task_id_set:
                raise LoopValidationError(
                    f"{path}: task {task.task_id!r} depends_on unknown task_id {dep!r}"
                )

    return loop


def check_acyclic(loop: LoopDefinition) -> None:
    """Kahn's algorithm over depends_on edges. Raises CyclicLoopError if a
    cycle is present (nodes remain after all zero-in-degree nodes are
    consumed).
    """
    task_ids = [t.task_id for t in loop.tasks]
    depends_on: dict[str, set[str]] = {t.task_id: set(t.depends_on) for t in loop.tasks}

    # in-degree here = number of unresolved dependencies each task has.
    in_degree = {tid: len(depends_on[tid]) for tid in task_ids}
    # dependents[x] = set of tasks that depend_on x
    dependents: dict[str, set[str]] = {tid: set() for tid in task_ids}
    for tid, deps in depends_on.items():
        for dep in deps:
            dependents.setdefault(dep, set()).add(tid)

    queue = [tid for tid in task_ids if in_degree[tid] == 0]
    visited: set[str] = set()

    while queue:
        node = queue.pop()
        visited.add(node)
        for dependent in dependents.get(node, set()):
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                queue.append(dependent)

    remaining = set(task_ids) - visited
    if remaining:
        raise CyclicLoopError(
            f"loop {loop.loop_id!r} contains a cycle among task(s): {sorted(remaining)}"
        )


def validate_loop_file(path: str | Path) -> LoopDefinition:
    """Combined schema-validate + acyclicity-check entrypoint."""
    loop = load_loop(path)
    check_acyclic(loop)
    return loop
