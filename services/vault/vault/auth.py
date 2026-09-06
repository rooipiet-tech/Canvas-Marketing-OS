"""TD-03 fix: shared-secret bearer-token authentication for every Vault
router (docs/architecture/09-technical-debt.md's TD-03,
docs/accepted-risks.md's "Risk: Vault API has no authentication/
authorization on any endpoint").

Every router except GET /health (vault/main.py — dependency-free by
design, AC-017) now depends on `require_service_token`, which validates an
`Authorization: Bearer <token>` header against a token resolved the SAME
two-step way vault/db.py resolves DATABASE_URL:

  1. The VAULT_API_TOKEN environment variable, if set directly — what
     every deployed Container App/Job actually gets, via
     infra/main.bicep's required, no-default `vaultApiToken` secure
     param (generated fresh per deploy, threaded to ca-vault and to
     every one of its 3 real callers — orchestrator, publisher,
     analytics-ingest — in the SAME `az deployment group create` call,
     so there is no cross-deploy staleness window).
  2. Otherwise, fetched from Key Vault (DefaultAzureCredential, secret
     name `vault-api-token`) — a fallback/manual-rotation path, never
     the production wiring.

Local dev / CI (neither of the above configured) falls back to
_DEV_DEFAULT_TOKEN — a committed, obviously-fake token, never a silent
"authentication disabled" state. L-0041's pattern for a committed
dev-only fallback secret applies here: a static warning (this docstring)
plus a runtime WARNING logged every time the fallback is actually the
token in effect (see `_resolve_expected_token` below). This is what lets
`uvicorn vault.main:app` run locally / `test_contract_smoke.py` run
against a bare `docker run` container with zero env vars, while a real
deploy — where VAULT_API_TOKEN is a required, no-default Bicep param —
can never silently end up on this value.

Comparison uses `hmac.compare_digest` (constant-time) so response timing
never leaks how many leading bytes of a guessed token matched.
"""

from __future__ import annotations

import asyncio
import hmac
import logging

from fastapi import Header, HTTPException

from .config import get_settings

logger = logging.getLogger("vault.auth")

# Obviously-fake, never a real credential — see module docstring's L-0041
# note. Never used when VAULT_API_TOKEN or the Key Vault secret is
# actually configured, which every real deploy guarantees (infra/main.bicep
# threads a required, no-default `vaultApiToken` param).
_DEV_DEFAULT_TOKEN = "vault-dev-only-insecure-default-token-do-not-use-in-prod"  # noqa: S105

_resolved_token: str | None = None
_resolved_token_lock = asyncio.Lock()


def _unauthorized(message: str) -> HTTPException:
    return HTTPException(
        status_code=401,
        detail={"error": {"message": message, "code": "unauthorized"}},
        headers={"WWW-Authenticate": "Bearer"},
    )


def _fetch_token_from_key_vault() -> str | None:
    """Mirrors vault/db.py's `_fetch_database_url_from_key_vault` — same
    DefaultAzureCredential + SecretClient pattern, different secret name.
    Returns None (never raises) if no Key Vault is configured or the
    secret doesn't exist there — that's a normal, expected state for local
    dev/CI, not an error; the caller falls back to _DEV_DEFAULT_TOKEN."""
    from azure.core.exceptions import ResourceNotFoundError
    from azure.identity import DefaultAzureCredential
    from azure.keyvault.secrets import SecretClient

    settings = get_settings()
    vault_url = settings.resolved_key_vault_url()
    if not vault_url:
        return None
    credential = DefaultAzureCredential()
    client = SecretClient(vault_url=vault_url, credential=credential)
    try:
        secret = client.get_secret(settings.api_token_secret_name)
    except ResourceNotFoundError:
        return None
    return secret.value


async def _resolve_expected_token() -> str:
    """Cached for the process lifetime — matches vault/db.py's pool-once
    pattern; a rotated token takes effect on the next container restart
    (every deploy forces one, per container-app.bicep's deployToken), not
    mid-process, which is an acceptable tradeoff for a value checked on
    every single request."""
    global _resolved_token
    if _resolved_token is not None:
        return _resolved_token
    async with _resolved_token_lock:
        if _resolved_token is None:
            settings = get_settings()
            token = settings.vault_api_token
            if not token:
                # Key Vault SDK calls are synchronous; run off the event
                # loop thread, same as db.py's _resolve_database_url.
                token = await asyncio.to_thread(_fetch_token_from_key_vault)
            if not token:
                logger.warning(
                    "VAULT_API_TOKEN is not configured and no Key Vault secret "
                    "resolved one — falling back to the committed dev-only "
                    "default token (L-0041). This must NEVER happen against a "
                    "real deploy: infra/main.bicep's vaultApiToken param has no "
                    "default and is required."
                )
                token = _DEV_DEFAULT_TOKEN
            _resolved_token = token
    return _resolved_token


async def require_service_token(authorization: str | None = Header(default=None)) -> None:
    """FastAPI dependency — wire onto every router except /health (see
    vault/main.py's `app.include_router(..., dependencies=[Depends(...)])`
    calls). Raises 401 on a missing/malformed/wrong Authorization header;
    returns None (allows the request through) on an exact match."""
    if not authorization:
        raise _unauthorized("missing Authorization header")
    scheme, _, credential = authorization.partition(" ")
    if scheme.lower() != "bearer" or not credential:
        raise _unauthorized("Authorization header must be 'Bearer <token>'")
    expected = await _resolve_expected_token()
    if not hmac.compare_digest(credential, expected):
        raise _unauthorized("invalid token")


def _reset_for_tests() -> None:
    """Test-only: drop the cached resolved token so a test can exercise a
    different VAULT_API_TOKEN env var / Key Vault fallback path. A real
    process never needs to invalidate this."""
    global _resolved_token
    _resolved_token = None
