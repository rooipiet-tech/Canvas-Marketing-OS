"""AC-04 — telemetry adoption across every Wave-1 service touches none of
the 9 frozen-contract files. Pure static check — no live infra needed,
runs unconditionally in CI and locally alike.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

FROZEN_FILES = [
    "contracts/gate-token/schema.json",
    "contracts/gate-token/spec.md",
    "contracts/model-gateway/openapi.yaml",
    "contracts/model-gateway/redaction-rules.yaml",
    "contracts/service-bus/spec.md",
    "contracts/service-bus/task-envelope.schema.json",
    "contracts/vault-schema/schema.sql",
    "contracts/orchestrator/loop-definition.schema.json",
    "contracts/orchestrator/heartbeat-event.schema.json",
]


def _git_diff_against_main(path: str) -> str:
    result = subprocess.run(
        ["git", "diff", "origin/main", "--", path],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    return result.stdout


def test_no_frozen_contract_file_changed() -> None:
    dirty = [f for f in FROZEN_FILES if _git_diff_against_main(f).strip()]
    assert not dirty, f"frozen contract file(s) changed this session: {dirty}"


def test_validate_contracts_script_reports_no_drift() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/validate_contracts.py"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"scripts/validate_contracts.py reported drift:\nSTDOUT:\n{result.stdout}\n"
        f"STDERR:\n{result.stderr}"
    )
