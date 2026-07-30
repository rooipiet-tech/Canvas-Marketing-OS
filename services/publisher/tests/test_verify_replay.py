"""AC-10 — a replayed jti is rejected with reason `token_replayed`."""

from __future__ import annotations

import base64

from app.jti_ledger import JtiLedger, consume_jti
from app.verifier import REASON_TOKEN_REPLAYED, GateTokenVerifier

ASSET_BYTES = b"replay-test-asset-bytes"


def test_rejects_replayed_token(client, conn, agent_run, gate_decision, make_token) -> None:
    from app.hashing import recompute_content_hash

    content_hash = recompute_content_hash(ASSET_BYTES)
    token, claims = make_token(gate_decision_id=gate_decision, content_hash=content_hash)

    payload = {
        "agent_run_id": str(agent_run),
        "function_id": "publish.social_post",
        "asset_bytes_b64": base64.b64encode(ASSET_BYTES).decode(),
        "gate_token": token,
    }

    first = client.post("/publish", json=payload)
    assert first.status_code == 200
    assert first.json()["outcome"] == "published"

    second = client.post("/publish", json=payload)
    assert second.status_code == 403
    body = second.json()["detail"]
    assert body["outcome"] == "rejected"
    assert body["reason"] == REASON_TOKEN_REPLAYED

    # The refusal is audited with the distinct reason.
    rows = conn.execute(
        "SELECT outcome, reason FROM governance.publish_attempts ORDER BY created_at, id"
    ).fetchall()
    assert [row["reason"] for row in rows][-1] == "token_replayed"
    assert [row["outcome"] for row in rows] == ["published", "rejected"]

    # The ledger holds exactly one row for that jti.
    ledger_rows = conn.execute(
        "SELECT jti FROM governance.jti_ledger WHERE jti = %s", (claims["jti"],)
    ).fetchall()
    assert len(ledger_rows) == 1


def test_ledger_claim_is_atomic_and_single_use(conn) -> None:
    ledger = JtiLedger(conn)
    jti = "jti-atomic-check"

    assert ledger.consume(jti) is True
    assert ledger.consume(jti) is False
    assert consume_jti(conn, jti) is False
    assert ledger.seen(jti) is True
    assert ledger.get(jti)["jti"] == jti

    assert ledger.consume("a-different-jti") is True


def test_verifier_holds_no_seen_jti_state(signing_keys) -> None:
    """The verifier must not carry replay state in the object itself."""
    _private, public_pem = signing_keys
    verifier = GateTokenVerifier(
        public_key_pem=public_pem, issuer="cmos-gatekeeper", audience="cmos-publisher"
    )
    state_blob = repr(verifier.__dict__)
    assert "jti" not in state_blob
    assert not any(isinstance(value, (set, frozenset)) for value in verifier.__dict__.values())
