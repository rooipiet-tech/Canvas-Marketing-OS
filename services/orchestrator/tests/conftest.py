"""Shared pytest fixtures for the orchestrator test suite.

Session-scoped default VAULT_API_URL=http://127.0.0.1:1 bakes AC-017 into
the harness itself: no test in this suite requires a live/reachable Vault
API. Tests that need a live Postgres connection (dependency-ordering,
kill-worker redelivery, dead-letter, status-endpoint-latency, etc.) are
skipped when $PG_URL / $DATABASE_URL is not set — their genuine
confirmed-runnable environment is the new orchestrator-test CI job
(services/orchestrator's tests running against CI's postgres: service
container), matching AC-015's own wording. Tests that don't need Postgres
(schema/acyclicity, decompose golden, backoff, envelope-contract,
vault-client-degrades) always run, including in this sandbox (no
Docker/psql) via the step-1 uv-provisioned Python 3.12 venv.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

os.environ.setdefault("VAULT_API_URL", "http://127.0.0.1:1")

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
ORCHESTRATOR_DIR = REPO_ROOT / "services" / "orchestrator"


def _pg_url() -> str | None:
    return os.environ.get("PG_URL") or os.environ.get("DATABASE_URL")


@pytest.fixture(scope="session")
def pg_url() -> str:
    url = _pg_url()
    if not url:
        pytest.skip(
            "PG_URL/DATABASE_URL not set — this test requires a live Postgres "
            "(CI's orchestrator-test job)"
        )
    return url


@pytest.fixture(scope="session")
def tmp_pg(pg_url: str) -> str:
    """Applies migrations/0001_orchestrator_init.sql fresh against pg_url
    (idempotent — safe to re-apply). Session-scoped so it only runs once
    per test session.
    """
    import psycopg

    migration_sql = (ORCHESTRATOR_DIR / "migrations" / "0001_orchestrator_init.sql").read_text(
        encoding="utf-8"
    )
    with psycopg.connect(pg_url) as conn:
        with conn.cursor() as cur:
            cur.execute(migration_sql)
        conn.commit()
    return pg_url


@pytest.fixture()
def clean_pg(tmp_pg: str) -> str:
    """Truncates orchestrator tables before each test that uses it."""
    import psycopg

    with psycopg.connect(tmp_pg) as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE task_transitions, task_state CASCADE")
        conn.commit()
    return tmp_pg


@pytest.fixture()
def sample_loop_daily():
    from orchestrator.loop_loader import load_loop

    return load_loop(ORCHESTRATOR_DIR / "loops" / "daily-signal-loop.yaml")


@pytest.fixture()
def sample_loop_weekly():
    from orchestrator.loop_loader import load_loop

    return load_loop(ORCHESTRATOR_DIR / "loops" / "weekly-content-loop.yaml")


@pytest.fixture()
def local_bus():
    from orchestrator.servicebus.local_double import InMemoryServiceBus

    return InMemoryServiceBus()


@pytest.fixture()
def fresh_uuid() -> str:
    return str(uuid.uuid4())
