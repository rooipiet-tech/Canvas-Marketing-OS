"""Shared Gatekeeper test fixtures (AC-19).

FK-ORDERED FIXTURES
-------------------
gate_decisions.agent_run_id is NOT NULL -> agent_runs.id, and
agent_runs.campaign_id is NOT NULL -> campaigns.id. Every fixture that
produces a gate_decisions row therefore builds the full chain
campaigns -> agent_runs -> gate_decisions first. `make_campaign`,
`make_agent_run` and `make_gate_decision` are declared below in exactly
that order, and no test is permitted to skip the agent_run fixture.

SKIP-CLEANLY-WHEN-NO-POSTGRES (L-0001)
--------------------------------------
If TEST_DATABASE_URL is unset or unreachable the database-backed fixtures
`pytest.skip(...)` rather than erroring, so a sandbox without Postgres
reports skips (not spurious failures). Pure-unit suites (policy loading,
token issuance, alg pinning) never touch these fixtures and always run.
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

SERVICE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]

if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

VAULT_SCHEMA_SQL = REPO_ROOT / "contracts" / "vault-schema" / "schema.sql"
GOVERNANCE_MIGRATION_SQL = (
    REPO_ROOT / "infra" / "modules" / "governance" / "migrations" / "0001_governance_init.sql"
)

# Truncated before every test so "exactly one row" assertions are exact.
# Order matters: governance first, then the public FK chain leaf-to-root.
GOVERNANCE_TABLES = (
    "governance.approval_actions",
    "governance.approval_inbox",
    "governance.publish_attempts",
    "governance.jti_ledger",
    "governance.kill_switches",
)
PUBLIC_TABLES = ("assets", "gate_decisions", "costs", "agent_runs", "campaigns")


def _dsn() -> str | None:
    return os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")


@pytest.fixture(scope="session")
def database_url() -> str:
    """Session-scoped: probe Postgres once and apply both schemas once."""
    dsn = _dsn()
    if not dsn:
        pytest.skip(
            "TEST_DATABASE_URL is not set — database-backed governance tests "
            "need a Postgres instance (see L-0001)"
        )

    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - environment guard
        pytest.skip(f"psycopg is not installed: {exc}")

    try:
        conn = psycopg.connect(dsn, autocommit=True, connect_timeout=5)
    except Exception as exc:  # pragma: no cover - environment guard
        pytest.skip(f"TEST_DATABASE_URL is unreachable ({exc}) — skipping DB-backed tests")

    try:
        conn.execute(VAULT_SCHEMA_SQL.read_text(encoding="utf-8"))
        conn.execute(GOVERNANCE_MIGRATION_SQL.read_text(encoding="utf-8"))
    finally:
        conn.close()

    # app.config reads this on every call; make sure the service under test
    # points at the same database the fixtures populate.
    os.environ["TEST_DATABASE_URL"] = dsn
    return dsn


@pytest.fixture(scope="session", autouse=True)
def _signing_key_for_session() -> str:
    """Stable RS256 test key for the whole session (SIGNER_BACKEND=local)."""
    from app.signer.local_signer import LocalRSASigner

    signer = LocalRSASigner()
    os.environ["SIGNER_BACKEND"] = "local"
    os.environ["GATE_SIGNING_PRIVATE_KEY_PEM"] = signer.private_key_pem()
    os.environ.setdefault("APPROVAL_BASE_URL", "https://approval.cmos-dev.example")
    os.environ.pop("TEAMS_WEBHOOK_URL", None)

    import app.signer as signer_module

    signer_module._CACHED_SIGNER = None
    return signer.private_key_pem()


@pytest.fixture
def conn(database_url: str):
    import psycopg
    from psycopg.rows import dict_row

    connection = psycopg.connect(database_url, autocommit=True, row_factory=dict_row)
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture(autouse=True)
def _clean_database(request: pytest.FixtureRequest):
    """Truncate governance + FK-chain tables before each DB-backed test."""
    if "database_url" not in request.fixturenames and "conn" not in request.fixturenames:
        yield
        return

    dsn = request.getfixturevalue("database_url")
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as cleaner:
        cleaner.execute(
            "TRUNCATE {} RESTART IDENTITY CASCADE".format(
                ", ".join(GOVERNANCE_TABLES + PUBLIC_TABLES)
            )
        )
    yield


# ---------------------------------------------------------------------
# FK chain factories — declared in insert order: campaigns -> agent_runs
# -> gate_decisions. Never build a gate_decisions row without both parents.
# ---------------------------------------------------------------------


@pytest.fixture
def make_campaign(conn):
    def _make(name: str | None = None) -> uuid.UUID:
        row = conn.execute(
            "INSERT INTO campaigns (name) VALUES (%s) RETURNING id",
            (name or f"campaign-{uuid.uuid4().hex[:8]}",),
        ).fetchone()
        return row["id"]

    return _make


@pytest.fixture
def make_agent_run(conn, make_campaign):
    def _make(
        campaign_id: uuid.UUID | None = None, agent_name: str = "gatekeeper-test"
    ) -> uuid.UUID:
        parent = campaign_id or make_campaign()
        row = conn.execute(
            "INSERT INTO agent_runs (campaign_id, agent_name) VALUES (%s, %s) RETURNING id",
            (parent, agent_name),
        ).fetchone()
        return row["id"]

    return _make


@pytest.fixture
def make_gate_decision(conn, make_agent_run):
    def _make(
        agent_run_id: uuid.UUID | None = None,
        *,
        decided_by: str = "gatekeeper:test",
        outcome: str = "approved",
        reason: str | None = None,
    ) -> uuid.UUID:
        parent = agent_run_id or make_agent_run()
        row = conn.execute(
            """
            INSERT INTO gate_decisions (agent_run_id, decided_by, outcome, reason)
            VALUES (%s, %s, %s, %s) RETURNING id
            """,
            (parent, decided_by, outcome, reason),
        ).fetchone()
        return row["id"]

    return _make


@pytest.fixture
def campaign(make_campaign) -> uuid.UUID:
    return make_campaign()


@pytest.fixture
def agent_run(make_agent_run, campaign) -> uuid.UUID:
    """The FK parent every gate_decisions-touching test must go through."""
    return make_agent_run(campaign)


@pytest.fixture
def client(database_url: str):
    """TestClient over the INTERNAL Gatekeeper app (main.py)."""
    from fastapi.testclient import TestClient
    from main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def approval_client(database_url: str):
    """TestClient over the EXTERNAL approval-action app (approval_main.py)."""
    from approval_main import app
    from fastapi.testclient import TestClient

    with TestClient(app) as test_client:
        yield test_client
