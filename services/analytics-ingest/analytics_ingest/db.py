"""analytics_ingest.db — thin asyncpg connection-pool wrapper.

DATABASE_URL is read exclusively from os.environ — no hardcoded
host/FQDN literal anywhere in this module (AC-30). Cross-service addresses
always come from env vars (populated by Bicep outputs at deploy time), per
L-0025.
"""

from __future__ import annotations

import os

import asyncpg

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    """Return a lazily-created, process-wide asyncpg connection pool."""
    global _pool
    if _pool is None:
        database_url = os.environ["DATABASE_URL"]
        _pool = await asyncpg.create_pool(database_url)
    return _pool


async def close_pool() -> None:
    """Close the process-wide pool, if one was ever created."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
