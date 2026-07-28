"""Lazy asyncpg connection pool with a Key Vault fallback for DATABASE_URL.

`/health` never touches this module (see vault/main.py) — the pool is
created lazily on first real DB use, not at process startup, so the
container can report healthy before the database is reachable.
"""

from __future__ import annotations

import asyncio
import json

import asyncpg

from .config import get_settings

_pool: asyncpg.Pool | None = None
_pool_lock = asyncio.Lock()


async def _init_connection(conn: asyncpg.Connection) -> None:
    # jsonb columns (signals.payload, agent_runs.input/output,
    # vault_internal.audit_log.detail) decode to native Python dicts
    # instead of raw JSON text, so FastAPI's jsonable_encoder serializes
    # them as nested objects rather than escaped strings.
    await conn.set_type_codec(
        "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog", format="text"
    )


def _fetch_database_url_from_key_vault() -> str:
    from azure.identity import DefaultAzureCredential
    from azure.keyvault.secrets import SecretClient

    settings = get_settings()
    vault_url = settings.resolved_key_vault_url()
    if not vault_url:
        raise RuntimeError(
            "DATABASE_URL is not set and no KEY_VAULT_NAME/KEY_VAULT_URL is "
            "configured to fetch it from — cannot resolve a database connection."
        )
    credential = DefaultAzureCredential()
    client = SecretClient(vault_url=vault_url, credential=credential)
    secret = client.get_secret(settings.db_connection_secret_name)
    return secret.value


async def _resolve_database_url() -> str:
    settings = get_settings()
    if settings.database_url:
        return settings.database_url
    # Key Vault SDK calls are synchronous; run off the event loop thread.
    return await asyncio.to_thread(_fetch_database_url_from_key_vault)


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is not None:
        return _pool
    async with _pool_lock:
        if _pool is None:
            database_url = await _resolve_database_url()
            _pool = await asyncpg.create_pool(
                database_url, min_size=1, max_size=20, init=_init_connection
            )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
