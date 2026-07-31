"""mcp_common.credentials — dual-mode secret resolution (AC-4, AC-6, AC-7).

Fixture vs. live mode is selected PURELY by whether a server's secret env
var is set — no code or config file edit is needed to switch modes.
Resolution order for `resolve_secret`:

  1. The secret's own env var (e.g. BUFFER_API_KEY, CANVA_CLIENT_ID). This
     is how the deployed Container App receives the value (via a Key-Vault
     -backed Container Apps secretRef wired in infra/modules/mcp — see
     container-app.bicep), and also how local/CI testing sets a dummy
     value to exercise the live code path (AC-4/AC-6).
  2. If unset, and KEY_VAULT_URI is also set, attempt a direct Key Vault
     SDK lookup (azure-identity + azure-keyvault-secrets) as a fallback
     resolution path. ANY failure here (auth, network, missing secret) is
     treated identically to "absent" — this function never raises — so a
     live smoke test run with no credentials configured never attempts a
     network call (AC-6) and always falls through cleanly to fixture mode.
  3. Otherwise: None (fixture mode).

Real vault secret names used by this build (per environment facts, not
docs/credentials-runbook.md's stale names): buffer-api-key,
canva-client-id, canva-client-secret. mcp-web has no vendor credential at
all (it is a fetch+rate-limit server, not a Buffer/Canva-style
integration) — its dual-mode gate is the non-secret MCP_WEB_LIVE_MODE flag
via `flag_enabled` below, per the orchestrator-approved waiver recorded in
.loop/spec.json's amendments (v2, addressing plan-reviewer finding F3).
"""

from __future__ import annotations

import os


def resolve_secret(env_var_name: str, vault_secret_name: str | None = None) -> str | None:
    """Resolve a secret's value, or None if absent (fixture mode)."""
    value = os.environ.get(env_var_name)
    if value:
        return value

    vault_uri = os.environ.get("KEY_VAULT_URI")
    if not vault_secret_name or not vault_uri:
        return None

    try:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient

        credential = DefaultAzureCredential()
        client = SecretClient(vault_url=vault_uri, credential=credential)
        secret = client.get_secret(vault_secret_name)
        return secret.value
    except Exception:
        # Never raise: absence of a reachable Key Vault must degrade to
        # fixture mode, not an error, per AC-6.
        return None


def is_live_mode(env_var_name: str, vault_secret_name: str | None = None) -> bool:
    """True iff a real (non-empty) secret value is resolvable."""
    return resolve_secret(env_var_name, vault_secret_name) is not None


def flag_enabled(env_var_name: str) -> bool:
    """Non-secret feature-flag equivalent of is_live_mode, for mcp-web.

    mcp-web has no vendor credential to gate on, so its fixture-vs-live
    switch is a plain boolean env var (MCP_WEB_LIVE_MODE) rather than a
    Key-Vault-backed secret. Truthy values: 1, true, yes, on
    (case-insensitive); anything else (including absent) is falsy.
    """
    value = os.environ.get(env_var_name, "").strip().lower()
    return value in ("1", "true", "yes", "on")
