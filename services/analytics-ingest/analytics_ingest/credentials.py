"""analytics_ingest.credentials — dual-mode secret resolution.

A local reimplementation of mcp/common/mcp_common/credentials.py's
resolve_secret/is_live_mode shape (env var first, then KEY_VAULT_URI +
DefaultAzureCredential + SecretClient fallback, any exception swallowed to
None, never raises) — reimplemented locally rather than importing /mcp,
since /mcp is outside this session's frozen touch-scope (services/
analytics-ingest/, analytics/, infra/modules/analytics/, infra/main.bicep
additive, services/orchestrator/loops/ one new file, docs/
credentials-runbook.md, docs/*analytics*.md, .github/workflows/
analytics-image.yml only).

Fixture vs. live mode is selected PURELY by whether a connector's secret
env var is set — no code or config file edit is needed to switch modes.
Resolution order for `resolve_secret`:

  1. The secret's own env var. This is how the deployed Container Apps Job
     receives the value (a Key-Vault-backed secretRef), and also how
     local/CI testing sets a dummy value to exercise the live code path.
  2. If unset, and KEY_VAULT_URI is also set, attempt a direct Key Vault
     SDK lookup (azure-identity + azure-keyvault-secrets) as a fallback
     resolution path. ANY failure here (auth, network, missing secret) is
     treated identically to "absent" — this function never raises — so a
     test run with no credentials configured never attempts a network call
     and always falls through cleanly to fixture mode (AC-27).
  3. Otherwise: None (fixture mode).
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
        # fixture mode, not an error (AC-27).
        return None


def is_live_mode(env_var_name: str, vault_secret_name: str | None = None) -> bool:
    """True iff a real (non-empty) secret value is resolvable."""
    return resolve_secret(env_var_name, vault_secret_name) is not None
