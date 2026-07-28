"""Gateway configuration — environment-driven, provider-agnostic.

Deliberately generic: no provider vendor name appears here. Provider-specific
environment variables (e.g. the upstream API key) are read inside the
corresponding adapter module under ``providers/``, so swapping or adding a
provider never requires editing this file.
"""

from __future__ import annotations

import os
from pathlib import Path

_TRUTHY = {"1", "true", "yes", "on"}


def env_bool(name: str, default: str = "false") -> bool:
    """Read a boolean feature flag from the environment."""
    return os.environ.get(name, default).strip().lower() in _TRUTHY


# Postgres connection string for the Vault database (costs / gate_decisions).
DATABASE_URL: str | None = os.environ.get("DATABASE_URL")

# Name of the Key Vault holding provider credentials. The Container App
# resolves the secret natively (see infra/modules/gateway.bicep) and projects
# it into the process environment, so the gateway never calls the vault's
# data plane itself.
KEY_VAULT_NAME: str | None = os.environ.get("KEY_VAULT_NAME")

# Feature flag for the "deliberate" reasoning hint. Defaults to disabled:
# while disabled, a request carrying ``deliberate: true`` gets a structured
# NOT_IMPLEMENTED error rather than being silently ignored. Flipping this
# flag later needs no caller-facing contract change.
DELIBERATE_FLAG_ENABLED: bool = env_bool("DELIBERATE_FLAG_ENABLED")


# Where the frozen contract files the gateway reads at RUNTIME live —
# ``<contracts_dir>/model-gateway/openapi.yaml`` (request-shape validation)
# and ``<contracts_dir>/model-gateway/redaction-rules.yaml`` (the redaction
# firewall).
#
# The default is the repo-root ``contracts/`` directory, computed relative to
# this file. That is correct — and needs zero configuration — whenever the
# process runs from a full repository checkout (local development, pytest,
# scripts/).
#
# It is NOT correct inside the container image: the image is built from the
# ``services/model-gateway`` directory alone, so there is no repository around
# the code and the two-levels-up walk lands outside anything populated. The
# image therefore stages just those two files and sets CONTRACTS_DIR to point
# at them (see Dockerfile + .github/workflows/deploy-gateway.yml).
#
# Read through a function rather than frozen into a module constant so a test
# (and an operator) can repoint it without re-importing the world.
#
# N4 — the fallback is computed INSIDE the function, in the branch that
# actually needs it, and never at import time. ``parents[2]`` is only defined
# for a file at least three directories below a filesystem root; inside the
# image the source sits at ``/app/config.py``, which has exactly two parents
# (``/app`` and ``/``), so evaluating it there raises IndexError. Eagerly
# computing it at module scope therefore crashed the whole app on import
# (main -> completion -> config) *even though* the Dockerfile sets
# CONTRACTS_DIR and the fallback was never wanted. Deferred like this, the
# walk is not evaluated at all in the container: the override branch returns
# first. Every prior test missed this because a repo checkout and pytest's
# tmp_path are both many levels deep, where parents[2] always exists.


def contracts_dir() -> Path:
    """Directory holding the frozen contract files, honouring CONTRACTS_DIR."""
    override = os.environ.get("CONTRACTS_DIR", "").strip()
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2] / "contracts"
