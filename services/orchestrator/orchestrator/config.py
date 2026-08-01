"""Centralized, non-crashing config reads for the orchestrator service.

Every value here is read with a safe default/None and only dereferenced
(and only fails loudly) at the call site that actually needs it. This is
what lets main.py's lifespan (AC-018) and worker.py degrade gracefully to
the local Service Bus double / a no-op DB layer instead of crashing
FastAPI startup when DATABASE_URL / SERVICE_BUS_NAMESPACE / VAULT_API_URL
are unset — required because AC-018's literal verify command runs
`uvicorn main:app` with none of those env vars set.
"""

from __future__ import annotations

import os
from pathlib import Path

DATABASE_URL: str | None = os.environ.get("DATABASE_URL")
VAULT_API_URL: str | None = os.environ.get("VAULT_API_URL")
SERVICE_BUS_NAMESPACE: str | None = os.environ.get("SERVICE_BUS_NAMESPACE")

# Worker loop poll interval, seconds. Small default keeps tests fast;
# overridden via env var in production if needed.
WORKER_POLL_INTERVAL_S: float = float(os.environ.get("WORKER_POLL_INTERVAL_S", "1.0"))

# Where the frozen contract files the orchestrator reads at RUNTIME live —
# ``<contracts_dir>/orchestrator/loop-definition.schema.json`` (loop_loader.py's
# JSON Schema validation of every shipped loop definition).
#
# Same pattern as services/model-gateway/config.py's contracts_dir() (see
# that file's header for the full incident writeup): the default below is
# the repo-root ``contracts/`` directory, computed relative to this file —
# correct, and needing zero configuration, whenever the process runs from a
# full repository checkout (local development, pytest, scripts/). It is NOT
# correct inside the container image: the image is built from
# ``services/orchestrator`` alone, so there is no repository around the
# code and the four-levels-up walk lands on the filesystem root, not a repo
# root — confirmed live (ca-orchestrator's own startup logs, 2026-07-31:
# ``loop_load_failed ... No such file or directory:
# '/contracts/orchestrator/loop-definition.schema.json'``, and the
# smoke-test job crashing before it could publish anything). The image
# therefore stages just the orchestrator schema files and sets
# CONTRACTS_DIR to point at them (see Dockerfile +
# .github/workflows/orchestrator-image.yml's staging step).
#
# Read through a function, evaluated only when actually called, rather than
# a module-level constant — deferring the fallback's parent-walk avoids
# ever evaluating it inside the container at all, where CONTRACTS_DIR is
# always set and the override branch returns first.
def contracts_dir() -> Path:
    """Directory holding the frozen contract files, honouring CONTRACTS_DIR."""
    override = os.environ.get("CONTRACTS_DIR", "").strip()
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[3] / "contracts"


# Where the numbered function packages dispatch.py's handlers read at
# RUNTIME live -- ``<functions_dir>/09-market-intelligence-director/
# fetch_sources.yaml``, ``.../prompt.md`` for functions 02/09/42, and
# functions/02-brand-steward-qa/permission_check.py (dynamically loaded,
# L-0039).
#
# SAME incident class as contracts_dir() directly above (L-0062):
# dispatch.py previously computed this at module level via
# ``Path(__file__).resolve().parents[3] / "functions"``, correct only in a
# full repository checkout. Caught by test_contracts_path_resolution.py's
# own sweep test (added by the contracts_dir() fix, plan step 25's rebase
# reconciliation pass) for the identical reason: the orchestrator image is
# built from services/orchestrator alone, so the four-levels-up walk lands
# outside any repository inside the container, and every one of dispatch.py's
# 5 real task-type handlers depends on reading a prompt/schema/permission
# file from here. Fixed identically: a function honouring FUNCTIONS_DIR,
# falling back to the checkout-relative path, with the image staging only
# the 3 function packages this service actually needs (functions/09-*,
# functions/42-*, functions/02-*) and setting FUNCTIONS_DIR to point at
# them (Dockerfile + .github/workflows/orchestrator-image.yml's staging
# step).
def functions_dir() -> Path:
    """Directory holding the numbered function packages, honouring FUNCTIONS_DIR."""
    override = os.environ.get("FUNCTIONS_DIR", "").strip()
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[3] / "functions"


# AC-10: total model cost for one daily loop run must be metered and
# reported below THIS configured budget. A single named source of truth,
# env-var-overridable with a documented default, cited by both the
# metering check (orchestrator/dispatch.py / tests/e2e) and
# docs/run-the-loop.md — never a bare literal duplicated in multiple
# places.
DAILY_LOOP_BUDGET_USD: float = float(os.environ.get("CMOS_DAILY_LOOP_BUDGET_USD", "5.00"))

# App Insights connection string for THIS service's tracer provider
# (telemetry_lib.exporter.configure_tracer_provider reads the same env var
# directly if none is passed explicitly — see telemetry_wiring.py).
APPLICATIONINSIGHTS_CONNECTION_STRING: str | None = os.environ.get(
    "APPLICATIONINSIGHTS_CONNECTION_STRING"
)
