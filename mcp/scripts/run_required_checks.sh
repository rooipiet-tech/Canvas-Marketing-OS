#!/usr/bin/env bash
# Canvas Marketing OS — mcp/scripts/run_required_checks.sh
#
# Runs every required pytest marker for the MCP tool plane and fails
# loudly (non-zero exit) if ANY test is skipped — a 0-passed/N-skipped
# run must never be mistaken for a real pass. This matters most for
# mcp_logging and mcp_agent_e2e, which silently SKIP (not fail) when no
# reachable Postgres is configured (see ../conftest.py's pg_conn /
# database_url fixtures) — exactly the gap plan-reviewer finding F2
# flagged. This script self-enforces mcp/README.md's "a skip is not a
# valid verification" rule mechanically (via `-rs` plus a grep-and-fail
# wrapper), rather than relying on a human reading the pytest summary
# line.
#
# Usage (from anywhere):
#   bash mcp/scripts/run_required_checks.sh
#
# Prerequisite: a reachable Postgres with DATABASE_URL (or PG_URL) set,
# and mcp/mcp_ops/schema.sql already appliable (conftest.py's pg_conn
# fixture applies it itself, idempotently) — see mcp/README.md's
# "Required local Postgres prerequisite" section. Without it, this
# script is EXPECTED to fail (non-zero exit) on mcp_logging/
# mcp_agent_e2e, by design — that failure is the self-enforcement working
# correctly, not a bug in this script.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MCP_ROOT="$(dirname "$SCRIPT_DIR")"
REPO_ROOT="$(dirname "$MCP_ROOT")"

MARKERS=(
  mcp_conformance
  mcp_buffer_surface
  mcp_mode_switch
  mcp_rate_limit
  mcp_smoke
  mcp_logging
  mcp_manifest_schema
  mcp_agent_e2e
  mcp_canva_surface
  mcp_web_allowlist
)

FAILED=0

for marker in "${MARKERS[@]}"; do
  echo "=== pytest -m $marker mcp/ -v ==="
  OUTPUT_FILE="$(mktemp)"
  set +e
  # Literal `mcp/` path argument, run from the repo root — exactly the
  # form .loop/spec.json's verify commands use for nearly every
  # criterion. Safe to pass explicitly (recursing into mcp-web/
  # mcp-buffer/mcp-canva) thanks to mcp/pytest.ini's
  # --import-mode=importlib + python_files=test_*.py: those settings
  # keep the three servers' identically-named standalone smoke_test.py
  # scripts (not pytest test modules — already exercised via
  # test_smoke.py's subprocess-based approach, AC-6) out of collection
  # entirely, so they never collide.
  (cd "$REPO_ROOT" && python -m pytest -m "$marker" mcp/ -rs -v) > "$OUTPUT_FILE" 2>&1
  STATUS=$?
  set -e
  cat "$OUTPUT_FILE"

  if [ "$STATUS" -ne 0 ]; then
    echo "FAIL: pytest -m $marker exited non-zero (status=$STATUS)."
    FAILED=1
  fi

  if grep -qE "[1-9][0-9]* skipped" "$OUTPUT_FILE"; then
    echo "FAIL: pytest -m $marker reported at least one SKIPPED test - a skip is not a valid pass for an official verification run (see mcp/README.md's Postgres prerequisite)."
    FAILED=1
  fi

  rm -f "$OUTPUT_FILE"
done

if [ "$FAILED" -ne 0 ]; then
  echo ""
  echo "run_required_checks.sh: one or more markers failed or reported a skip. See above."
  exit 1
fi

echo ""
echo "run_required_checks.sh: all markers passed with zero skips."
