"""Shared Publisher test fixtures (AC-19).

Mirrors services/gatekeeper/tests/conftest.py deliberately — the two
services share no library, so the duplication is intentional and the
parity of behaviour is asserted by
services/gatekeeper/tests/test_kill_switch_parity.py.

FK-ORDERED FIXTURES: `make_campaign` -> `make_agent_run` ->
`make_gate_decision`, declared in that order. Every publish attempt is
bound to a real gate_decisions row, which requires the full
campaigns -> agent_runs chain first.

SKIP-CLEANLY-WHEN-NO-POSTGRES (L-0001): DB-backed fixtures skip rather
than error when TEST_DATABASE_URL is unset or unreachable.
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

SERVICE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]

if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

VAULT_SCHEMA_SQL = REPO_ROOT / "contracts" / "vault-schema" / "schema.sql"
GOVERNANCE_MIGRATION_SQL = (
    REPO_ROOT / "infra" / "modules" / "governance" / "migrations" / "0001_governance_init.sql"
)

GOVERNANCE_TABLES = (
    "governance.approval_actions",
    "governance.approval_inbox",
    "governance.publish_attempts",
    "governance.jti_ledger",
    "governance.kill_switches",
)
PUBLIC_TABLES = ("assets", "gate_decisions", "costs", "agent_runs", "campaigns")

ISSUER = "cmos-gatekeeper"
AUDIENCE = "cmos-publisher"

CANONICAL_SEPARATORS = (",", ":")


def _dsn() -> str | None:
    return os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")


@pytest.fixture(scope="session")
def database_url() -> str:
    dsn = _dsn()
    if not dsn:
        pytest.skip(
            "TEST_DATABASE_URL is not set — database-backed publish tests need "
            "a Postgres instance (see L-0001)"
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

    os.environ["TEST_DATABASE_URL"] = dsn
    return dsn


@pytest.fixture(scope="session", autouse=True)
def signing_keys() -> tuple[str, str]:
    """One RS256 keypair for the session; the public half goes to the app."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    os.environ["GATE_TOKEN_PUBLIC_KEY_PEM"] = public_pem
    os.environ["GATE_TOKEN_ISSUER"] = ISSUER
    os.environ["GATE_TOKEN_AUDIENCE"] = AUDIENCE
    return private_pem, public_pem


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


@pytest.fixture(autouse=True)
def _reset_vault_adapter():
    """The stub Vault-recording adapter is a spy — reset between tests."""
    from app.vault_adapter import get_vault_adapter

    get_vault_adapter().reset()
    yield
    get_vault_adapter().reset()


# ---------------------------------------------------------------------
# FK chain factories, in insert order.
# ---------------------------------------------------------------------


@pytest.fixture
def make_campaign(conn):
    def _make(name: str | None = None) -> uuid.UUID:
        return conn.execute(
            "INSERT INTO campaigns (name) VALUES (%s) RETURNING id",
            (name or f"campaign-{uuid.uuid4().hex[:8]}",),
        ).fetchone()["id"]

    return _make


@pytest.fixture
def make_agent_run(conn, make_campaign):
    def _make(campaign_id: uuid.UUID | None = None) -> uuid.UUID:
        parent = campaign_id or make_campaign()
        return conn.execute(
            "INSERT INTO agent_runs (campaign_id, agent_name) VALUES (%s, %s) RETURNING id",
            (parent, "publisher-test"),
        ).fetchone()["id"]

    return _make


@pytest.fixture
def make_gate_decision(conn, make_agent_run):
    def _make(
        agent_run_id: uuid.UUID | None = None,
        *,
        decided_by: str = "gatekeeper:policy",
        outcome: str = "approved",
        reason: str | None = "level_3_auto_approved",
    ) -> uuid.UUID:
        parent = agent_run_id or make_agent_run()
        return conn.execute(
            """
            INSERT INTO gate_decisions (agent_run_id, decided_by, outcome, reason)
            VALUES (%s, %s, %s, %s) RETURNING id
            """,
            (parent, decided_by, outcome, reason),
        ).fetchone()["id"]

    return _make


@pytest.fixture
def campaign(make_campaign) -> uuid.UUID:
    return make_campaign()


@pytest.fixture
def agent_run(make_agent_run, campaign) -> uuid.UUID:
    return make_agent_run(campaign)


@pytest.fixture
def gate_decision(make_gate_decision, agent_run) -> uuid.UUID:
    return make_gate_decision(agent_run)


@pytest.fixture
def make_token(signing_keys):
    """Mint an RS256 gate token with a canonical-JSON resource claim."""
    private_pem, _public_pem = signing_keys

    def _make(
        *,
        gate_decision_id: str | uuid.UUID,
        content_hash: str,
        function_id: str = "publish.social_post",
        subject: str | None = None,
        jti: str | None = None,
        issued_at: int | None = None,
        ttl_seconds: int = 900,
        algorithm: str = "RS256",
        key: str | None = None,
        **claim_overrides,
    ) -> tuple[str, dict]:
        now = int(issued_at if issued_at is not None else time.time())
        claims = {
            "iss": ISSUER,
            "sub": subject or str(uuid.uuid4()),
            "aud": AUDIENCE,
            "iat": now,
            "exp": now + ttl_seconds,
            "jti": jti or str(uuid.uuid4()),
            "gate_decision_id": str(gate_decision_id),
            "resource": json.dumps(
                {"content_hash": content_hash, "function_id": function_id},
                sort_keys=True,
                separators=CANONICAL_SEPARATORS,
            ),
        }
        claims.update(claim_overrides)
        token = jwt.encode(claims, key or private_pem, algorithm=algorithm)
        return token, claims

    return _make


@pytest.fixture
def client(database_url: str, signing_keys):
    from fastapi.testclient import TestClient
    from main import app

    with TestClient(app) as test_client:
        yield test_client
