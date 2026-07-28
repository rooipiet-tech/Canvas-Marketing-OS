"""Full contract-smoke suite for the Vault service (AC-002 through AC-009,
AC-013, AC-015).

Run against a live Vault endpoint:

    VAULT_BASE_URL=http://ca-vault.internal.<env>.azurecontainerapps.io \\
    DATABASE_URL=postgresql://... \\
    pytest services/vault/tests/test_contract_smoke.py -v

VAULT_BASE_URL defaults to http://localhost:8000 (local dev / docker run).
DATABASE_URL is optional: tests that need out-of-band verification the
public API deliberately does not expose (elapsed retention windows,
direct audit_log/consent_linkage row checks) connect to Postgres directly
and are skipped if DATABASE_URL is not set — this is the same in-VNet
direct-DB-access pattern as caj-vault-query, not a contract API surface.

This is the exact suite infra/modules/vault/smoke-test-job.bicep's
caj-vault-smoke-test Container Apps Job runs in dev (plan step 19/25/30).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import time
import uuid
from datetime import date

import httpx
import pytest
import pytest_asyncio

BASE_URL = os.environ.get("VAULT_BASE_URL", "http://localhost:8000")
DATABASE_URL = os.environ.get("DATABASE_URL")
BULK_INSERT_COUNT = int(os.environ.get("VAULT_BULK_INSERT_COUNT", "1000"))
BULK_INSERT_CONCURRENCY = int(os.environ.get("VAULT_BULK_INSERT_CONCURRENCY", "50"))
BULK_INSERT_BUDGET_SECONDS = 60.0

pytestmark = pytest.mark.asyncio

TAXONOMY_FIELDS = [
    "vertical",
    "function_id",
    "campaign",
    "evidence_grade",
    "consent_status",
    "retention_class",
]


def taxonomy(campaign_id: str | None = None, **overrides) -> dict:
    base = {
        "vertical": "mobility",
        "function_id": "smoke-test-v1",
        "campaign": campaign_id or str(uuid.uuid4()),
        "evidence_grade": "B",
        "consent_status": "not_required",
        "retention_class": "standard_1y",
    }
    base.update(overrides)
    return base


requires_database = pytest.mark.skipif(
    not DATABASE_URL, reason="DATABASE_URL not set — skipping direct-DB verification test"
)


@pytest_asyncio.fixture(scope="session")
async def client():
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30.0) as c:
        yield c


@pytest_asyncio.fixture(scope="session")
async def db_conn():
    if not DATABASE_URL:
        yield None
        return
    import asyncpg

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        yield conn
    finally:
        await conn.close()


async def create_campaign(client: httpx.AsyncClient) -> str:
    payload = {"name": f"smoke-campaign-{uuid.uuid4()}", **taxonomy()}
    r = await client.post("/campaigns", json=payload)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def create_agent_run(client: httpx.AsyncClient, campaign_id: str) -> str:
    payload = {"agent_name": "smoke-agent", **taxonomy(campaign_id)}
    r = await client.post("/agent-runs", json=payload)
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest_asyncio.fixture(scope="session")
async def campaign_id(client: httpx.AsyncClient) -> str:
    return await create_campaign(client)


@pytest_asyncio.fixture(scope="session")
async def agent_run_id(client: httpx.AsyncClient, campaign_id: str) -> str:
    return await create_agent_run(client, campaign_id)


# ---------------------------------------------------------------------
# health
# ---------------------------------------------------------------------


async def test_health(client: httpx.AsyncClient):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


# ---------------------------------------------------------------------
# AC-002: taxonomy write-boundary validation
# ---------------------------------------------------------------------

# (path, campaign-aware, extra required business fields builder)
SIMPLE_OBJECT_TYPES = [
    ("signals", False, lambda cid: {"source": "semrush", "signal_type": "keyword_spike"}),
    ("opportunity-cards", True, lambda cid: {"title": "Smoke opportunity"}),
    ("briefs", True, lambda cid: {"title": "Smoke brief"}),
]


@pytest.mark.parametrize("path,campaign_aware,fields_fn", SIMPLE_OBJECT_TYPES)
@pytest.mark.parametrize("missing_field", TAXONOMY_FIELDS)
async def test_taxonomy_rejects_missing_field(
    client: httpx.AsyncClient, campaign_id: str, path, campaign_aware, fields_fn, missing_field
):
    payload = {**fields_fn(campaign_id), **taxonomy(campaign_id)}
    del payload[missing_field]
    r = await client.post(f"/{path}", json=payload)
    assert r.status_code == 422, r.text
    body = r.json()
    assert body["error"]["field"] == missing_field


@pytest.mark.parametrize("path,campaign_aware,fields_fn", SIMPLE_OBJECT_TYPES)
async def test_create_and_roundtrip_taxonomy(
    client: httpx.AsyncClient, campaign_id: str, path, campaign_aware, fields_fn
):
    payload = {**fields_fn(campaign_id), **taxonomy(campaign_id)}
    r = await client.post(f"/{path}", json=payload)
    assert r.status_code == 201, r.text
    created = r.json()

    r = await client.get(f"/{path}/{created['id']}")
    assert r.status_code == 200, r.text
    fetched = r.json()
    for field_name in TAXONOMY_FIELDS:
        assert str(fetched[field_name]) == str(payload[field_name]), field_name


# ---------------------------------------------------------------------
# AC-003: campaign -> real campaign_id FK persistence
# ---------------------------------------------------------------------


@pytest.mark.parametrize("path", ["opportunity-cards", "briefs", "agent-runs"])
async def test_campaign_persisted_to_real_fk(client: httpx.AsyncClient, campaign_id: str, path):
    fields = {
        "opportunity-cards": {"title": "x"},
        "briefs": {"title": "x"},
        "agent-runs": {"agent_name": "x"},
    }
    payload = {**fields[path], **taxonomy(campaign_id)}
    r = await client.post(f"/{path}", json=payload)
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["campaign"] == campaign_id
    assert (
        created.get("campaign_id") in (campaign_id, None)
        or str(created.get("campaign_id")) == campaign_id
    )

    r = await client.get(f"/{path}/{created['id']}")
    assert r.json()["campaign"] == campaign_id


@requires_database
async def test_campaign_persisted_queryable_via_db(
    db_conn, client: httpx.AsyncClient, campaign_id: str
):
    payload = {"title": "db-check brief", **taxonomy(campaign_id)}
    r = await client.post("/briefs", json=payload)
    assert r.status_code == 201, r.text
    brief_id = r.json()["id"]
    row = await db_conn.fetchrow(
        "SELECT campaign_id FROM briefs WHERE id = $1", uuid.UUID(brief_id)
    )
    assert str(row["campaign_id"]) == campaign_id


# ---------------------------------------------------------------------
# AC-004: consent gating for client-derived writes
# ---------------------------------------------------------------------


async def test_consent_gate_rejects_without_active_consent(
    client: httpx.AsyncClient, campaign_id: str
):
    subject = f"subject-{uuid.uuid4()}"
    payload = {
        "source": "semrush",
        "signal_type": "client_derived_signal",
        "data_subject_ref": subject,
        "consent_channel": "email",
        "consent_purpose": "campaign_targeting",
        **taxonomy(campaign_id),
    }
    r = await client.post("/signals", json=payload)
    assert 400 <= r.status_code < 500, r.text

    check = await client.get(
        "/consent/check",
        params={"data_subject_ref": subject, "channel": "email", "purpose": "campaign_targeting"},
    )
    assert check.json()["consented"] is False


async def test_consent_gate_allows_with_active_consent(client: httpx.AsyncClient, campaign_id: str):
    subject = f"subject-{uuid.uuid4()}"
    consent_payload = {
        "data_subject_ref": subject,
        "lawful_basis": "consent",
        "channel": "email",
        "purpose": "campaign_targeting",
        **taxonomy(campaign_id, consent_status="granted"),
    }
    r = await client.post("/consent-register", json=consent_payload)
    assert r.status_code == 201, r.text

    payload = {
        "source": "semrush",
        "signal_type": "client_derived_signal",
        "data_subject_ref": subject,
        "consent_channel": "email",
        "consent_purpose": "campaign_targeting",
        **taxonomy(campaign_id),
    }
    r = await client.post("/signals", json=payload)
    assert r.status_code == 201, r.text

    check = await client.get(
        "/consent/check",
        params={"data_subject_ref": subject, "channel": "email", "purpose": "campaign_targeting"},
    )
    assert check.json()["consented"] is True


@requires_database
async def test_consent_linkage_row_written(db_conn, client: httpx.AsyncClient, campaign_id: str):
    subject = f"subject-{uuid.uuid4()}"
    consent_payload = {
        "data_subject_ref": subject,
        "lawful_basis": "consent",
        "channel": "sms",
        "purpose": "campaign_targeting",
        **taxonomy(campaign_id, consent_status="granted"),
    }
    r = await client.post("/consent-register", json=consent_payload)
    assert r.status_code == 201, r.text

    payload = {
        "source": "semrush",
        "signal_type": "client_derived_signal",
        "data_subject_ref": subject,
        "consent_channel": "sms",
        "consent_purpose": "campaign_targeting",
        **taxonomy(campaign_id),
    }
    r = await client.post("/signals", json=payload)
    assert r.status_code == 201, r.text
    signal_id = r.json()["id"]

    row = await db_conn.fetchrow(
        "SELECT consent_register_id FROM vault_internal.consent_linkage "
        "WHERE object_table = 'signals' AND object_id = $1",
        uuid.UUID(signal_id),
    )
    assert row is not None


# ---------------------------------------------------------------------
# AC-005/AC-006: asset chain byte-identical retrieval + dedup
# ---------------------------------------------------------------------


async def test_asset_chain_byte_identical(
    client: httpx.AsyncClient, campaign_id: str, agent_run_id: str
):
    v1_bytes = f"v1-content-{uuid.uuid4()}".encode()
    v1_payload = {
        "asset_type": "social_post_image",
        "agent_run_id": agent_run_id,
        "content_base64": base64.b64encode(v1_bytes).decode(),
        **taxonomy(campaign_id),
    }
    r = await client.post("/assets", json=v1_payload)
    assert r.status_code == 201, r.text
    v1 = r.json()
    v1_hash = hashlib.sha256(v1_bytes).hexdigest()
    assert v1["content_hash"] == v1_hash

    v1_patch = await client.patch(f"/assets/{v1['id']}", json={"approval_state": "approved"})
    assert v1_patch.status_code == 200, v1_patch.text

    v2_bytes = f"v2-content-{uuid.uuid4()}".encode()
    v2_payload = {
        "asset_type": "social_post_image",
        "agent_run_id": agent_run_id,
        "predecessor_asset_id": v1["id"],
        "content_base64": base64.b64encode(v2_bytes).decode(),
        **taxonomy(campaign_id),
    }
    r = await client.post("/assets", json=v2_payload)
    assert r.status_code == 201, r.text
    v2 = r.json()
    v2_patch = await client.patch(f"/assets/{v2['id']}", json={"approval_state": "approved"})
    assert v2_patch.status_code == 200, v2_patch.text

    r = await client.get(f"/assets/{v1['id']}")
    assert r.status_code == 200, r.text
    fetched_v1 = r.json()
    fetched_bytes = base64.b64decode(fetched_v1["content_base64"])
    assert hashlib.sha256(fetched_bytes).hexdigest() == v1_hash
    assert fetched_bytes == v1_bytes


async def test_asset_dedup_no_duplicate_blob(
    client: httpx.AsyncClient, campaign_id: str, agent_run_id: str
):
    content = f"dedup-content-{uuid.uuid4()}".encode()
    content_b64 = base64.b64encode(content).decode()
    payload = {
        "asset_type": "social_post_image",
        "agent_run_id": agent_run_id,
        "content_base64": content_b64,
        **taxonomy(campaign_id),
    }
    r1 = await client.post("/assets", json=payload)
    assert r1.status_code == 201, r1.text
    r2 = await client.post("/assets", json=payload)
    assert r2.status_code == 201, r2.text

    a1, a2 = r1.json(), r2.json()
    assert a1["content_hash"] == a2["content_hash"]
    assert a1["storage_uri"] == a2["storage_uri"]
    assert a1["id"] != a2["id"]  # two asset rows, one blob


# ---------------------------------------------------------------------
# AC-007: retention-class expiry job (requires direct-DB seeding of an
# elapsed window — the API deliberately does not expose "set expires_at
# to the past" as a contract concept).
# ---------------------------------------------------------------------


@requires_database
async def test_retention_expiry_deletes_expired_objects(
    db_conn, client: httpx.AsyncClient, campaign_id: str, agent_run_id: str
):
    signal_payload = {
        "source": "smoke",
        "signal_type": "retention_test",
        **taxonomy(campaign_id, retention_class="ephemeral_30d"),
    }
    r = await client.post("/signals", json=signal_payload)
    assert r.status_code == 201, r.text
    signal_id = r.json()["id"]

    content = f"retention-asset-{uuid.uuid4()}".encode()
    asset_payload = {
        "asset_type": "retention_test",
        "agent_run_id": agent_run_id,
        "content_base64": base64.b64encode(content).decode(),
        **taxonomy(campaign_id, retention_class="ephemeral_30d"),
    }
    r = await client.post("/assets", json=asset_payload)
    assert r.status_code == 201, r.text
    asset = r.json()
    content_hash = asset["content_hash"]

    for object_table, object_id in (("signals", signal_id), ("assets", asset["id"])):
        await db_conn.execute(
            """
            UPDATE vault_internal.retention_policy
            SET expires_at = now() - interval '1 day'
            WHERE object_table = $1 AND object_id = $2
            """,
            object_table,
            uuid.UUID(object_id),
        )

    r = await client.post("/retention-expiry-runs")
    assert r.status_code == 202, r.text

    r = await client.get(f"/signals/{signal_id}")
    assert r.status_code == 404
    r = await client.get(f"/assets/{asset['id']}")
    assert r.status_code == 404

    for object_table, object_id in (("signals", signal_id), ("assets", asset["id"])):
        row = await db_conn.fetchrow(
            """
            SELECT object_id, reason, detail FROM vault_internal.audit_log
            WHERE event_type = 'retention_deletion' AND object_table = $1 AND object_id = $2
            ORDER BY occurred_at DESC LIMIT 1
            """,
            object_table,
            uuid.UUID(object_id),
        )
        assert row is not None, f"no retention_deletion audit row for {object_table}/{object_id}"

    asset_audit = await db_conn.fetchrow(
        """
        SELECT detail FROM vault_internal.audit_log
        WHERE event_type = 'retention_deletion' AND object_table = 'assets' AND object_id = $1
        ORDER BY occurred_at DESC LIMIT 1
        """,
        uuid.UUID(asset["id"]),
    )
    assert content_hash in str(asset_audit["detail"])


# ---------------------------------------------------------------------
# AC-008/AC-021: utilisation rollup
# ---------------------------------------------------------------------


async def test_utilisation_rollup(client: httpx.AsyncClient, campaign_id: str):
    r = await client.post(
        "/opportunity-cards", json={"title": "rollup-test", **taxonomy(campaign_id)}
    )
    assert r.status_code == 201, r.text
    oc_id = r.json()["id"]

    r = await client.post(
        "/signals", json={"source": "x", "signal_type": "y", **taxonomy(campaign_id)}
    )
    assert r.status_code == 201, r.text
    signal_id = r.json()["id"]

    for _ in range(3):
        r = await client.get(
            f"/opportunity-cards/{oc_id}", headers={"X-Caller-Service": "smoke-test-svc-a"}
        )
        assert r.status_code == 200
    for _ in range(2):
        r = await client.get(
            f"/signals/{signal_id}", headers={"X-Caller-Service": "smoke-test-svc-b"}
        )
        assert r.status_code == 200

    today = date.today().isoformat()
    r = await client.post("/utilisation/rollup", json={"date": today})
    assert r.status_code == 200, r.text

    r = await client.get("/utilisation/rollup", params={"from": today, "to": today})
    assert r.status_code == 200, r.text
    rows = r.json()
    matches = [
        row
        for row in rows
        if row["object_class"] == "opportunity_cards"
        and row["caller_service"] == "smoke-test-svc-a"
    ]
    assert matches and matches[0]["read_count"] >= 3

    r = await client.get(
        "/utilisation/rollup", params={"from": today, "to": today, "object_class": "signals"}
    )
    assert all(row["object_class"] == "signals" for row in r.json())


# ---------------------------------------------------------------------
# AC-013: 1,000-object bulk insert under 60s (bounded-concurrency async
# client, working with the server's batched multi-row INSERT).
# ---------------------------------------------------------------------


async def test_bulk_insert_under_budget(client: httpx.AsyncClient, campaign_id: str):
    semaphore = asyncio.Semaphore(BULK_INSERT_CONCURRENCY)

    async def create_one(i: int) -> int:
        async with semaphore:
            payload = {
                "source": "bulk-smoke",
                "signal_type": f"bulk-{i}",
                **taxonomy(campaign_id),
            }
            r = await client.post("/signals", json=payload)
            return r.status_code

    start = time.monotonic()
    results = await asyncio.gather(*(create_one(i) for i in range(BULK_INSERT_COUNT)))
    elapsed = time.monotonic() - start

    assert all(status == 201 for status in results), "not all bulk-insert requests succeeded"
    assert elapsed < BULK_INSERT_BUDGET_SECONDS, (
        f"{BULK_INSERT_COUNT}-object bulk insert took {elapsed:.1f}s, budget is "
        f"{BULK_INSERT_BUDGET_SECONDS}s"
    )


# ---------------------------------------------------------------------
# AC-015: every operationId in contracts/vault-api.yaml is reachable
# ---------------------------------------------------------------------


async def test_gate_decision_and_cost_append_only(
    client: httpx.AsyncClient, campaign_id: str, agent_run_id: str
):
    r = await client.post(
        "/gate-decisions",
        json={
            "agent_run_id": agent_run_id,
            "decided_by": "smoke-test",
            "outcome": "approved",
            **taxonomy(campaign_id),
        },
    )
    assert r.status_code == 201, r.text
    gd_id = r.json()["id"]
    r = await client.get(f"/gate-decisions/{gd_id}")
    assert r.status_code == 200

    r = await client.post(
        "/costs",
        json={
            "agent_run_id": agent_run_id,
            "provider": "anthropic",
            "amount": 0.05,
            **taxonomy(campaign_id),
        },
    )
    assert r.status_code == 201, r.text
    cost_id = r.json()["id"]
    r = await client.get(f"/costs/{cost_id}")
    assert r.status_code == 200


async def test_taxonomy_immutable_on_patch(client: httpx.AsyncClient, campaign_id: str):
    r = await client.post(
        "/signals", json={"source": "x", "signal_type": "y", **taxonomy(campaign_id)}
    )
    assert r.status_code == 201, r.text
    signal_id = r.json()["id"]

    r = await client.patch(f"/signals/{signal_id}", json={"vertical": "different-vertical"})
    assert r.status_code == 422, r.text

    r = await client.patch(f"/signals/{signal_id}", json={"source": "updated-source"})
    assert r.status_code == 200, r.text
    assert r.json()["source"] == "updated-source"


async def test_delete_object(client: httpx.AsyncClient, campaign_id: str):
    r = await client.post(
        "/signals", json={"source": "x", "signal_type": "y", **taxonomy(campaign_id)}
    )
    assert r.status_code == 201, r.text
    signal_id = r.json()["id"]
    r = await client.delete(f"/signals/{signal_id}")
    assert r.status_code == 204
    r = await client.get(f"/signals/{signal_id}")
    assert r.status_code == 404


async def test_list_endpoints(client: httpx.AsyncClient):
    for path in [
        "signals",
        "campaigns",
        "opportunity-cards",
        "briefs",
        "assets",
        "agent-runs",
        "gate-decisions",
        "costs",
        "consent-register",
        "retention-expiry-runs",
    ]:
        r = await client.get(f"/{path}")
        assert r.status_code == 200, f"{path}: {r.text}"
        assert isinstance(r.json(), list)
