#!/usr/bin/env python3
"""Validate orchestrator loop-definition YAML files (schema + acyclicity).

Standalone, deps pyyaml + jsonschema only. Reuses
services/orchestrator/orchestrator/loop_loader.py's functions via a
sys.path insertion (import, not a duplicate implementation).

Usage (from repo root):
    python scripts/validate_loops.py \\
        services/orchestrator/loops/daily-signal-loop.yaml \\
        services/orchestrator/loops/weekly-content-loop.yaml

Exits 0 and prints "PASS: <path>" per file on success. Exits 1 with a
clear message on the first failure.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "services" / "orchestrator"))

from orchestrator.loop_loader import validate_loop_file  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print(
            "usage: python scripts/validate_loops.py <loop.yaml> [<loop.yaml> ...]",
            file=sys.stderr,
        )
        return 1

    for path in argv:
        try:
            loop = validate_loop_file(path)
        except Exception as exc:  # noqa: BLE001 - report and exit, not a crash
            print(f"FAIL: {path}: {exc}", file=sys.stderr)
            return 1
        print(f"PASS: {path} (loop_id={loop.loop_id}, {len(loop.tasks)} tasks)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
