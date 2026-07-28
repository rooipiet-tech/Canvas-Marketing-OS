#!/usr/bin/env python3
"""Validate Canvas Marketing OS contracts (contracts/**).

Run standalone (from repo root):

    python scripts/validate_contracts.py

Checks performed:
  1. contracts/model-gateway/openapi.yaml is a valid OpenAPI 3.1 document
     with >=1 path (via openapi_spec_validator).
  2. contracts/service-bus/task-envelope.schema.json and
     contracts/gate-token/schema.json are both structurally valid JSON
     Schema (Draft 2020-12) documents (via jsonschema.check_schema).
  3. The gate-token schema's `required` array enforces the hard security
     rule: exp (bounded validity) + (jti or nonce) (replay prevention) +
     a resource/gate_decision-binding claim.
  4. Version anchors are present: OpenAPI info.version, and both JSON
     Schemas' $id contain a `/v1/`-style version segment.
  5. A breaking-change guard: contracts/.frozen-v1.sha256 pins a SHA-256
     hash per frozen contract file. If a frozen file's content changes
     without also bumping its version anchor, this script fails loudly
     with the file name, rather than silently letting a breaking change
     through CI. (First run creates the baseline file; see
     regenerate_baseline() below / the --write-baseline flag.)

Exits 0 and prints PASS on success. Exits non-zero with a clear message
on any failure.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import NoReturn

import yaml
from jsonschema import Draft202012Validator
from openapi_spec_validator import validate as validate_openapi

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTRACTS_DIR = REPO_ROOT / "contracts"

OPENAPI_PATH = CONTRACTS_DIR / "model-gateway" / "openapi.yaml"
VAULT_OPENAPI_PATH = CONTRACTS_DIR / "vault-api.yaml"
TASK_ENVELOPE_SCHEMA_PATH = CONTRACTS_DIR / "service-bus" / "task-envelope.schema.json"
GATE_TOKEN_SCHEMA_PATH = CONTRACTS_DIR / "gate-token" / "schema.json"
BASELINE_PATH = CONTRACTS_DIR / ".frozen-v1.sha256"

# session/s2-vault: vault-api.yaml is deliberately NOT added to FROZEN_FILES
# below — it is a new, actively-developed contract for this build, not a
# frozen-v1 baseline. It is still validated structurally (check_openapi,
# parametrized) and checked for internal-schema-name leakage
# (check_no_internal_leak), just not breaking-change-guarded.

# Frozen v1 contract files tracked by the breaking-change baseline guard.
# Paths are relative to CONTRACTS_DIR, using forward slashes, so the hash
# file is stable across platforms.
FROZEN_FILES = [
    "model-gateway/openapi.yaml",
    "vault-schema/schema.sql",
    "service-bus/task-envelope.schema.json",
    "service-bus/spec.md",
    "gate-token/schema.json",
    "gate-token/spec.md",
    "model-gateway/redaction-rules.yaml",
]

VERSION_SEGMENT_RE = re.compile(r"/v\d+(?:[./]|$)")


def fail(message: str) -> NoReturn:
    print(f"FAIL: {message}", file=sys.stderr)
    sys.exit(1)


def check_openapi(path: Path = OPENAPI_PATH) -> str:
    if not path.exists():
        fail(f"missing {path}")
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    version = str(doc.get("openapi", ""))
    if not version.startswith("3.1"):
        fail(f"{path} openapi field must start with 3.1, got {version!r}")
    paths = doc.get("paths") or {}
    if len(paths) < 1:
        fail(f"{path} must declare at least one path")
    info_version = doc.get("info", {}).get("version")
    if not info_version:
        fail(f"{path} missing info.version (required version anchor)")
    validate_openapi(doc)
    return str(info_version)


# session/s2-vault: vault-api.yaml must never leak the vault_internal
# sidecar-schema implementation detail into the public contract (see
# .loop/spec.json AC-023 / OQ-1-RESOLVED condition 2).
INTERNAL_LEAK_RE = re.compile(r"vault_internal|sidecar", re.IGNORECASE)


def check_no_internal_leak(path: Path) -> None:
    if not path.exists():
        fail(f"missing {path}")
    text = path.read_text(encoding="utf-8")
    if INTERNAL_LEAK_RE.search(text):
        fail(
            f"{path} references an internal schema name ('vault_internal' or "
            "'sidecar') — taxonomy/consent/rollup must be presented as "
            "first-class API concepts, not internal storage details"
        )


def check_json_schema(path: Path) -> dict:
    if not path.exists():
        fail(f"missing {path}")
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    schema_id = schema.get("$id", "")
    if not VERSION_SEGMENT_RE.search(schema_id):
        fail(f"{path} $id {schema_id!r} missing a /vN/ version segment")
    return schema


def check_gate_token_required_claims(schema: dict) -> None:
    required = set(schema.get("required", []))
    if "exp" not in required:
        fail("gate-token schema.json required[] must include 'exp' (bounded validity)")
    if not ({"jti", "nonce"} & required):
        fail(
            "gate-token schema.json required[] must include 'jti' or 'nonce' "
            "(single-use / replay prevention)"
        )
    if not any(("gate_decision" in claim or "resource" in claim) for claim in required):
        fail(
            "gate-token schema.json required[] must include a resource/"
            "gate_decision-binding claim (e.g. 'gate_decision_id')"
        )


def compute_baseline() -> dict[str, str]:
    baseline = {}
    for rel_path in FROZEN_FILES:
        full_path = CONTRACTS_DIR / rel_path
        if not full_path.exists():
            fail(f"frozen contract file missing: contracts/{rel_path}")
        # Normalize CRLF -> LF before hashing so the digest matches what's
        # actually committed (LF) regardless of the local checkout's line
        # -ending behavior. Without this, a Windows checkout with
        # core.autocrlf=true hashes CRLF-converted working-tree bytes that
        # never match a baseline recorded from LF content, and this guard
        # fails on every frozen file even though nothing actually changed —
        # confirmed live: CI (Linux runners, LF checkouts) stays green while
        # this exact failure reproduces locally on Windows.
        digest = hashlib.sha256(full_path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        baseline[rel_path] = digest
    return baseline


def write_baseline(baseline: dict[str, str]) -> None:
    lines = [f"{digest}  {rel_path}" for rel_path, digest in sorted(baseline.items())]
    BASELINE_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def read_baseline() -> dict[str, str]:
    baseline: dict[str, str] = {}
    if not BASELINE_PATH.exists():
        return baseline
    for line in BASELINE_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        digest, rel_path = line.split(None, 1)
        baseline[rel_path.strip()] = digest.strip()
    return baseline


def check_breaking_change_guard() -> None:
    current = compute_baseline()

    if not BASELINE_PATH.exists():
        # First run: establish the baseline from current contract contents.
        write_baseline(current)
        print(f"created baseline {BASELINE_PATH.relative_to(REPO_ROOT)}")
        return

    recorded = read_baseline()
    changed = [
        rel_path
        for rel_path, digest in current.items()
        if rel_path in recorded and recorded[rel_path] != digest
    ]
    if changed:
        fail(
            "breaking-change guard: the following frozen v1 contract file(s) "
            "changed content without a recorded baseline update — bump the "
            "version anchor (OpenAPI info.version / schema $id) to a new /v2/ "
            "namespace for a non-additive change, or intentionally refresh "
            "contracts/.frozen-v1.sha256 (via --write-baseline) for an "
            "additive/no-op change: " + ", ".join(changed)
        )

    # New files not yet recorded (e.g. added since baseline) are allowed
    # through without failing — only *changes to existing frozen entries*
    # trip the guard.


def main() -> None:
    write_baseline_flag = "--write-baseline" in sys.argv

    info_version = check_openapi()
    vault_info_version = check_openapi(VAULT_OPENAPI_PATH)
    check_no_internal_leak(VAULT_OPENAPI_PATH)

    task_envelope_schema = check_json_schema(TASK_ENVELOPE_SCHEMA_PATH)
    gate_token_schema = check_json_schema(GATE_TOKEN_SCHEMA_PATH)

    check_gate_token_required_claims(gate_token_schema)

    if write_baseline_flag:
        write_baseline(compute_baseline())
        print(f"baseline rewritten at {BASELINE_PATH.relative_to(REPO_ROOT)}")
    else:
        check_breaking_change_guard()

    print(f"model-gateway openapi info.version={info_version}")
    print(f"vault-api openapi info.version={vault_info_version}")
    print(f"task-envelope schema $id={task_envelope_schema['$id']}")
    print(f"gate-token schema $id={gate_token_schema['$id']}")
    print("PASS")


if __name__ == "__main__":
    main()
