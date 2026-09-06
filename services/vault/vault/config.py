"""Vault service configuration.

DATABASE_URL resolution order (see vault/db.py for the lazy connection
that actually uses this):
  1. The DATABASE_URL environment variable, if set directly (used by
     local dev / CI / the smoke-test and retention jobs, which receive it
     as a plain env var or Container Apps secretRef — same pattern as
     migration-job.bicep/vault-query-job.bicep).
  2. Otherwise, fetched from Key Vault at request time using the
     container's system-assigned managed identity (DefaultAzureCredential)
     — secret name `vault-db-connection-string` in the vault named by
     KEY_VAULT_NAME/KEY_VAULT_URL. Never a client secret (OIDC/managed
     identity only, per spec concurrency_discipline).

VAULT_API_TOKEN resolution (TD-03, see vault/auth.py for the dependency
that actually uses this) follows the EXACT SAME two-step order as
DATABASE_URL above: the VAULT_API_TOKEN env var (what every deployed
Container App/Job gets, via infra/main.bicep's required, no-default
`vaultApiToken` secure param — never optional in a real deploy), else a
Key Vault fetch of `vault-api-token` — kept only as a fallback / manual-
rotation path, since production wiring always supplies the env var
directly.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    # Direct connection string (local/dev/CI/job path). Optional — falls
    # back to Key Vault lookup at first DB use if unset.
    database_url: str | None = None

    # Key Vault fallback path.
    key_vault_name: str | None = None
    key_vault_url: str | None = None
    db_connection_secret_name: str = "vault-db-connection-string"

    # TD-03: shared-secret bearer token every router (except /health)
    # requires. Direct env var (production path) or Key Vault fallback,
    # same two-step resolution as database_url above.
    vault_api_token: str | None = None
    api_token_secret_name: str = "vault-api-token"

    # Content-addressed asset blob storage.
    storage_account_name: str | None = None
    blob_container_name: str = "vault-assets"

    def resolved_key_vault_url(self) -> str | None:
        if self.key_vault_url:
            return self.key_vault_url
        if self.key_vault_name:
            return f"https://{self.key_vault_name}.vault.azure.net"
        return None


@lru_cache
def get_settings() -> Settings:
    return Settings()
