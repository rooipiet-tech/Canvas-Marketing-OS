"""Gateway configuration — environment-driven, provider-agnostic.

Deliberately generic: no provider vendor name appears here. Provider-specific
environment variables (e.g. the upstream API key) are read inside the
corresponding adapter module under ``providers/``, so swapping or adding a
provider never requires editing this file.
"""

from __future__ import annotations

import os

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
