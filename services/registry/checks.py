"""Generic rubric check kinds shared by every function package.

A rubric entry's `check.kind` is dispatched here first. Anything not
implemented here is delegated to the owning package's own `tool_check.py`,
which is where function-specific semantics belong.

Every check is deterministic and returns `(passed, detail)`. `detail` is
written to be readable in a CI log without opening the task file.
"""

from __future__ import annotations

import json
import re
from typing import Any

GENERIC_KINDS = frozenset(
    {
        "contains_all",
        "contains_any",
        "not_contains",
        "regex",
        "min_words",
        "max_words",
        "json_valid",
        "json_array_min_len",
        "json_array_max_len",
        "json_field_equals",
    }
)


def _resolve(document: Any, path: str) -> Any:
    """Resolve a dotted path such as `signals` or `verdict.pass`."""
    current = document
    if not path:
        return current
    for part in path.split("."):
        if isinstance(current, list):
            current = current[int(part)]
        elif isinstance(current, dict):
            current = current[part]
        else:
            raise KeyError(path)
    return current


def _as_json(output: str) -> Any:
    return json.loads(output)


def apply_generic_check(check: dict, output: str) -> tuple[bool, str]:
    """Run a generic check kind against a model output string."""
    kind = check.get("kind")

    if kind == "contains_all":
        values = [str(value) for value in check.get("values", [])]
        missing = [value for value in values if value not in output]
        return not missing, (
            f"all {len(values)} required substring(s) present"
            if not missing
            else f"missing required substring(s): {missing}"
        )

    if kind == "contains_any":
        values = [str(value) for value in check.get("values", [])]
        found = [value for value in values if value in output]
        return bool(found), (
            f"found {found}" if found else f"none of the required substrings present: {values}"
        )

    if kind == "not_contains":
        values = [str(value) for value in check.get("values", [])]
        present = [value for value in values if value in output]
        return not present, (
            f"none of the {len(values)} banned substring(s) present"
            if not present
            else f"banned substring(s) present: {present}"
        )

    if kind == "regex":
        pattern = str(check.get("pattern", ""))
        minimum = int(check.get("min_matches", 1))
        flags = re.IGNORECASE if check.get("ignore_case") else 0
        matches = re.findall(pattern, output, flags)
        return len(matches) >= minimum, (
            f"pattern {pattern!r} matched {len(matches)} time(s), need >= {minimum}"
        )

    if kind == "min_words":
        value = int(check.get("value", 0))
        count = len(output.split())
        return count >= value, f"word count is {count}, need >= {value}"

    if kind == "max_words":
        value = int(check.get("value", 0))
        count = len(output.split())
        return count <= value, f"word count is {count}, need <= {value}"

    if kind == "json_valid":
        try:
            _as_json(output)
        except json.JSONDecodeError as exc:
            return False, f"output is not a single valid JSON document: {exc}"
        return True, "output parses as a single JSON document"

    if kind in {"json_array_min_len", "json_array_max_len", "json_field_equals"}:
        try:
            document = _as_json(output)
        except json.JSONDecodeError as exc:
            return False, f"output is not valid JSON ({exc})"
        path = str(check.get("path", ""))
        try:
            target = _resolve(document, path)
        except (KeyError, IndexError, ValueError):
            return False, f"path {path!r} is not present in the output"

        if kind == "json_field_equals":
            expected = check.get("value")
            return target == expected, f"{path} is {target!r}, expected {expected!r}"

        if not isinstance(target, list):
            return False, f"path {path!r} resolves to {type(target).__name__}, expected an array"
        if kind == "json_array_min_len":
            minimum = int(check.get("min", 1))
            return len(target) >= minimum, (
                f"{path} has {len(target)} item(s), need >= {minimum}"
            )
        maximum = int(check.get("max", 0))
        return len(target) <= maximum, f"{path} has {len(target)} item(s), need <= {maximum}"

    return False, f"unknown generic check kind: {kind!r}"
