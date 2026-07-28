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


async def create_campaign(client: httpx.AsyncClient, db_conn=None) -> str:
    """Creates a new campaign.

    AC-002's taxonomy fields (including `campaign`) apply uniformly across
    all 9 object types with no per-type exemption (see
    vault/routers/objects.py insert_object()) -- so a "campaigns" object's
    own `campaign` taxonomy field must, like every other type, reference
    an EXISTING campaigns.id (vault_internal.object_taxonomy.campaign_id
    carries a real FK to public.campaigns(id)). We reuse any
    already-existing campaign (the live dev DB is not reset between
    runs), which is what every non-bootstrap call site here actually
    needs. Only the very first campaign ever created against a
    completely empty database has nothing to reference -- for that one
    bootstrap case, if `db_conn` (direct-DB access, same pattern as the
    other `requires_database` checks in this file) is available, seed
    exactly one campaign row directly via SQL rather than through the API
    (the generic create-object path has no bootstrap special-case of its
    own, by design -- AC-002). Without DATABASE_URL, that one bootstrap
    case cannot be worked around from this test harness; see the
    build's self_flags for this known limitation.
    """
    existing = await client.get("/campaigns", params={"limit": 1})
    rows = existing.json() if existing.status_code == 200 else []
    if rows:
        reference_campaign_id = rows[0]["id"]
    elif db_conn is not None:
        seed_id = uuid.uuid4()
        await db_conn.execute(
            "INSERT INTO campaigns (id, name) VALUES ($1, $2)",
            seed_id,
            f"bootstrap-campaign-{seed_id}",
        )
        await db_conn.execute(
            """
            INSERT INTO vault_internal.object_taxonomy
                (object_table, object_id, vertical, function_id, campaign_id,
                 evidence_grade, consent_status, retention_class)
            VALUES ('campaigns', $1, 'mobility', 'bootstrap-seed', $1,
                     'B', 'not_required', 'standard_1y')
            ON CONFLICT (object_table, object_id) DO NOTHING
            """,
            seed_id,
        )
        await db_conn.execute(
            """
            INSERT INTO vault_internal.retention_policy
                (object_table, object_id, retention_class, expires_at)
            VALUES ('campaigns', $1, 'standard_1y', now() + interval '365 days')
            ON CONFLICT (object_table, object_id) DO NOTHING
            """,
            seed_id,
        )
        reference_campaign_id = str(seed_id)
    else:
        reference_campaign_id = str(uuid.uuid4())  # will 422 on a truly empty DB w/o DATABASE_URL
    payload = {"name": f"smoke-campaign-{uuid.uuid4()}", **taxonomy(reference_campaign_id)}
    r = await client.post("/campaigns", json=payload)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def create_agent_run(client: httpx.AsyncClient, campaign_id: str) -> str:
    payload = {"agent_name": "smoke-agent", **taxonomy(campaign_id)}
    r = await client.post("/agent-runs", json=payload)
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest_asyncio.fixture(scope="session")
async def campaign_id(client: httpx.AsyncClient, db_conn) -> str:
    return await create_campaign(client, db_conn=db_conn)


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

# (path, campaign-aware, extra required business fields builder(campaign_id,
# agent_run_id) -- covers all 9 object types (previously only 3 of 9 were
# covered here, per .loop/review.json F6 / AGENT-NATIVE-001). `campaigns`
# reuses the already-existing `campaign_id` fixture as ITS OWN `campaign`
# taxonomy value -- see create_campaign()'s docstring for why a
# "campaigns" object's `campaign` field must reference an existing
# campaigns.id like every other type, no special-casing.
SIMPLE_OBJECT_TYPES = [
    ("signals", False, lambda cid, aid: {"source": "semrush", "signal_type": "keyword_spike"}),
    ("campaigns", False, lambda cid, aid: {"name": f"tax-test-campaign-{uuid.uuid4()}"}),
    ("opportunity-cards", True, lambda cid, aid: {"title": "Smoke opportunity"}),
    ("briefs", True, lambda cid, aid: {"title": "Smoke brief"}),
    ("agent-runs", True, lambda cid, aid: {"agent_name": "smoke-agent-tax"}),
    (
        "assets",
        True,
        lambda cid, aid: {
            "asset_type": "social_post_image",
            "agent_run_id": aid,
            "content_base64": base64.b64encode(f"tax-{uuid.uuid4()}".encode()).decode(),
        },
    ),
    (
        "gate-decisions",
        False,
        lambda cid, aid: {"agent_run_id": aid, "decided_by": "smoke-test", "outcome": "approved"},
    ),
    (
        "costs",
        False,
        lambda cid, aid: {"agent_run_id": aid, "provider": "anthropic", "amount": 0.05},
    ),
    (
        "consent-register",
        False,
        lambda cid, aid: {
            "data_subject_ref": f"subject-{uuid.uuid4()}",
            "lawful_basis": "consent",
            "channel": "email",
            "purpose": "taxonomy-coverage-test",
        },
    ),
]


@pytest.mark.parametrize("path,campaign_aware,fields_fn", SIMPLE_OBJECT_TYPES)
@pytest.mark.parametrize("missing_field", TAXONOMY_FIELDS)
async def test_taxonomy_rejects_missing_field(
    client: httpx.AsyncClient,
    campaign_id: str,
    agent_run_id: str,
    path,
    campaign_aware,
    fields_fn,
    missing_field,
):
    payload = {**fields_fn(campaign_id, agent_run_id), **taxonomy(campaign_id)}
    del payload[missing_field]
    r = await client.post(f"/{path}", json=payload)
    assert r.status_code == 422, r.text
    body = r.json()
    assert body["error"]["field"] == missing_field


@pytest.mark.parametrize("path,campaign_aware,fields_fn", SIMPLE_OBJECT_TYPES)
async def test_create_and_roundtrip_taxonomy(
    client: httpx.AsyncClient,
    campaign_id: str,
    agent_run_id: str,
    path,
    campaign_aware,
    fields_fn,
):
    payload = {**fields_fn(campaign_id, agent_run_id), **taxonomy(campaign_id)}
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


# ---------------------------------------------------------------------
# AC-009/AC-015 coverage extension: GET/PATCH/DELETE-by-id cases for
# every operationId that previously had zero non-interactive coverage
# (.loop/lenses.json AGENT-NATIVE-001 -- 14 of 47 operationIds were never
# called: deleteAgentRun, deleteAsset, deleteBrief, deleteCampaign,
# deleteOpportunityCard, getAgentRun, getCampaign,
# getConsentRegisterEntry, getRetentionExpiryRun,
# revokeConsentRegisterEntry, updateAgentRun, updateBrief, updateCampaign,
# updateOpportunityCard).
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "path,create_fields,patch_body",
    [
        (
            "campaigns",
            lambda cid, aid: {"name": f"crud-campaign-{uuid.uuid4()}"},
            {"status": "active"},
        ),
        (
            "opportunity-cards",
            lambda cid, aid: {"title": "crud-opportunity"},
            {"status": "qualified"},
        ),
        ("briefs", lambda cid, aid: {"title": "crud-brief"}, {"body": "updated body"}),
        (
            "agent-runs",
            lambda cid, aid: {"agent_name": "crud-agent"},
            {"output": {"ok": True}},
        ),
    ],
)
async def test_get_update_delete_object(
    client: httpx.AsyncClient, campaign_id: str, agent_run_id: str, path, create_fields, patch_body
):
    """Covers getCampaign/updateCampaign/deleteCampaign,
    getOpportunityCard/updateOpportunityCard/deleteOpportunityCard,
    updateBrief/deleteBrief (getBrief already covered by
    test_create_and_roundtrip_taxonomy), and
    getAgentRun/updateAgentRun/deleteAgentRun."""
    payload = {**create_fields(campaign_id, agent_run_id), **taxonomy(campaign_id)}
    r = await client.post(f"/{path}", json=payload)
    assert r.status_code == 201, r.text
    obj_id = r.json()["id"]

    r = await client.get(f"/{path}/{obj_id}")
    assert r.status_code == 200, r.text

    r = await client.patch(f"/{path}/{obj_id}", json=patch_body)
    assert r.status_code == 200, r.text
    for key, value in patch_body.items():
        assert r.json()[key] == value

    r = await client.delete(f"/{path}/{obj_id}")
    assert r.status_code == 204, r.text
    r = await client.get(f"/{path}/{obj_id}")
    assert r.status_code == 404


async def test_asset_delete(client: httpx.AsyncClient, campaign_id: str, agent_run_id: str):
    """Covers deleteAsset."""
    content = f"delete-me-{uuid.uuid4()}".encode()
    payload = {
        "asset_type": "social_post_image",
        "agent_run_id": agent_run_id,
        "content_base64": base64.b64encode(content).decode(),
        **taxonomy(campaign_id),
    }
    r = await client.post("/assets", json=payload)
    assert r.status_code == 201, r.text
    asset_id = r.json()["id"]

    r = await client.delete(f"/assets/{asset_id}")
    assert r.status_code == 204, r.text
    r = await client.get(f"/assets/{asset_id}")
    assert r.status_code == 404


async def test_consent_register_get_and_revoke(client: httpx.AsyncClient, campaign_id: str):
    """Covers getConsentRegisterEntry and revokeConsentRegisterEntry
    (PATCH /consent-register/{id} setting revoked_at)."""
    subject = f"subject-{uuid.uuid4()}"
    payload = {
        "data_subject_ref": subject,
        "lawful_basis": "consent",
        "channel": "web",
        "purpose": "crud-coverage",
        **taxonomy(campaign_id, consent_status="granted"),
    }
    r = await client.post("/consent-register", json=payload)
    assert r.status_code == 201, r.text
    entry_id = r.json()["id"]

    r = await client.get(f"/consent-register/{entry_id}")
    assert r.status_code == 200, r.text
    assert r.json()["revoked_at"] is None

    import datetime as _dt

    revoked_at = _dt.datetime.now(_dt.timezone.utc).isoformat()
    r = await client.patch(f"/consent-register/{entry_id}", json={"revoked_at": revoked_at})
    assert r.status_code == 200, r.text
    assert r.json()["revoked_at"] is not None

    check = await client.get(
        "/consent/check",
        params={"data_subject_ref": subject, "channel": "web", "purpose": "crud-coverage"},
    )
    assert check.json()["consented"] is False


async def test_get_retention_expiry_run(client: httpx.AsyncClient):
    """Covers getRetentionExpiryRun."""
    r = await client.post("/retention-expiry-runs")
    assert r.status_code == 202, r.text
    run_id = r.json()["id"]

    r = await client.get(f"/retention-expiry-runs/{run_id}")
    assert r.status_code == 200, r.text
    assert r.json()["id"] == run_id


# ---------------------------------------------------------------------
# Fix #2 (AC-004): /assets create is client-derived-consent-gated exactly
# like every other object type -- previously the consent gate was never
# wired into build_assets_router().create() at all, so a client-derived
# asset upload with no active consent bypassed the check entirely.
# ---------------------------------------------------------------------


async def test_asset_consent_gate_rejects_without_active_consent(
    client: httpx.AsyncClient, campaign_id: str, agent_run_id: str
):
    subject = f"subject-{uuid.uuid4()}"
    content = f"no-consent-{uuid.uuid4()}".encode()
    payload = {
        "asset_type": "social_post_image",
        "agent_run_id": agent_run_id,
        "content_base64": base64.b64encode(content).decode(),
        "data_subject_ref": subject,
        "consent_channel": "email",
        "consent_purpose": "campaign_targeting",
        **taxonomy(campaign_id),
    }
    r = await client.post("/assets", json=payload)
    assert 400 <= r.status_code < 500, r.text

    check = await client.get(
        "/consent/check",
        params={"data_subject_ref": subject, "channel": "email", "purpose": "campaign_targeting"},
    )
    assert check.json()["consented"] is False


@requires_database
async def test_asset_consent_rejection_writes_audit_row(
    db_conn, client: httpx.AsyncClient, campaign_id: str, agent_run_id: str
):
    subject = f"subject-{uuid.uuid4()}"
    content = f"no-consent-audited-{uuid.uuid4()}".encode()
    payload = {
        "asset_type": "social_post_image",
        "agent_run_id": agent_run_id,
        "content_base64": base64.b64encode(content).decode(),
        "data_subject_ref": subject,
        "consent_channel": "email",
        "consent_purpose": "campaign_targeting",
        **taxonomy(campaign_id),
    }
    r = await client.post("/assets", json=payload)
    assert 400 <= r.status_code < 500, r.text

    row = await db_conn.fetchrow(
        """
        SELECT id FROM vault_internal.audit_log
        WHERE event_type = 'consent_rejected' AND object_table = 'assets'
          AND data_subject_ref = $1
        ORDER BY occurred_at DESC LIMIT 1
        """,
        subject,
    )
    assert row is not None, "no consent_rejected audit row written for a rejected asset create"


# ---------------------------------------------------------------------
# Fix #3 (AC-004): POST /consent-register succeeds without needing any
# pre-existing consent -- previously handle_consent_gate() applied to
# consent_register creation itself, making it structurally impossible to
# ever grant a subject's first consent through the API.
# ---------------------------------------------------------------------


async def test_consent_register_create_needs_no_preexisting_consent(
    client: httpx.AsyncClient, campaign_id: str
):
    subject = f"subject-{uuid.uuid4()}"
    # Deliberately do NOT create any prior consent_register row for this
    # subject/channel/purpose -- if consent_register creation were still
    # gated on itself, this would 403 with consent_required.
    payload = {
        "data_subject_ref": subject,
        "lawful_basis": "consent",
        "channel": "phone",
        "purpose": "first_ever_grant",
        **taxonomy(campaign_id, consent_status="granted"),
    }
    r = await client.post("/consent-register", json=payload)
    assert r.status_code == 201, r.text

    check = await client.get(
        "/consent/check",
        params={"data_subject_ref": subject, "channel": "phone", "purpose": "first_ever_grant"},
    )
    assert check.json()["consented"] is True
