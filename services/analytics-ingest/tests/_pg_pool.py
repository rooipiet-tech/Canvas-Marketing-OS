"""Session-scoped, real asyncpg connection pool for analytics-ingest's OWN
`analytics` Postgres schema.

Deliberately isolated into its own file, with zero external-service
references of any kind: this is where the real `asyncpg.create_pool(...)`
call for analytics-ingest's own schema lives, kept separate from
conftest.py's Vault-REST-API-mocking fixtures so the two concerns never
mix in the same file (AC-06's intent — "no direct psycopg/asyncpg
connection to the Vault's own database" — is best kept unambiguous by
construction: this module never mentions anything about the Vault
service at all, and only ever touches Postgres for the schema this
session owns).
"""

from __future__ import annotations

import os
from pathlib import Path

import asyncpg
import pytest_asyncio

MIGRATIONS_DIR = Path(__file__).parent.parent / "migrations"
MIGRATION_SQL = (MIGRATIONS_DIR / "0001_analytics_init.sql").read_text(encoding="utf-8")


@pytest_asyncio.fixture
async def _migrated_pool():
    """Function-scoped (not session-scoped): pytest-asyncio's default asyncio
    mode gives every test its own event loop, and an asyncpg pool/connection
    is bound to the loop it was created on — a session-scoped pool created on
    test 1's loop then reused by test 2's (different) loop raises
    "attached to a different loop" / "another operation is in progress".
    Re-applying the idempotent migration SQL per test is cheap; matching the
    fixture's loop scope to the tests' default loop scope is what matters.
    """
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"])
    async with pool.acquire() as conn:
        # Applied twice, back to back, to prove the migration is idempotent.
        await conn.execute(MIGRATION_SQL)
        await conn.execute(MIGRATION_SQL)
    yield pool
    await pool.close()
