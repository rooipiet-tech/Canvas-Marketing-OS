"""AC-09 — one changed byte is refused with `content_hash_mismatch`.

Publisher recomputes the digest over the raw asset bytes at publish time
and compares it with the hash bound into the token's canonical-JSON
`resource` claim. It never trusts a caller-supplied hash, and never reads
the nullable assets.content_hash column.
"""

from __future__ import annotations

import base64

APPROVED_BYTES = b"The quick brown fox jumps over the lazy dog."
# Exactly one byte differs ('T' -> 't').
TAMPERED_BYTES = b"the quick brown fox jumps over the lazy dog."


def _publish(client, agent_run, token, asset_bytes, **extra):
    payload = {
        "agent_run_id": str(agent_run),
        "function_id": "publish.social_post",
        "asset_bytes_b64": base64.b64encode(asset_bytes).decode(),
        "gate_token": token,
    }
    payload.update(extra)
    return client.post("/publish", json=payload)


def test_rejects_one_byte_tamper(client, conn, agent_run, gate_decision, make_token) -> None:
    from app.hashing import recompute_content_hash
    from app.vault_adapter import get_vault_adapter

    assert len(APPROVED_BYTES) == len(TAMPERED_BYTES)
    assert sum(a != b for a, b in zip(APPROVED_BYTES, TAMPERED_BYTES)) == 1

    approved_hash = recompute_content_hash(APPROVED_BYTES)
    token, claims = make_token(gate_decision_id=gate_decision, content_hash=approved_hash)

    response = _publish(client, agent_run, token, TAMPERED_BYTES)
    assert response.status_code == 403

    body = response.json()["detail"]
    assert body["reason"] == "content_hash_mismatch"
    assert body["reason"] not in {"token_absent", "token_expired", "token_replayed"}
    # The recorded hash is the one Publisher actually computed.
    assert body["content_hash"] == recompute_content_hash(TAMPERED_BYTES)
    assert body["content_hash"] != approved_hash

    rows = conn.execute("SELECT outcome, reason FROM governance.publish_attempts").fetchall()
    assert len(rows) == 1
    assert rows[0]["reason"] == "content_hash_mismatch"
    assert get_vault_adapter().call_count == 0

    # The jti is not burned by a mismatch, so the untampered bytes still work.
    assert (
        conn.execute(
            "SELECT count(*) AS n FROM governance.jti_ledger WHERE jti = %s", (claims["jti"],)
        ).fetchone()["n"]
        == 0
    )
    good = _publish(client, agent_run, token, APPROVED_BYTES)
    assert good.status_code == 200
    assert good.json()["outcome"] == "published"


def test_caller_supplied_hash_is_never_trusted(
    client, conn, agent_run, gate_decision, make_token
) -> None:
    """A caller claiming the approved hash for tampered bytes gets nowhere.

    The request model has no content_hash field at all; even smuggling one
    in as an extra JSON key changes nothing, because the comparison uses
    the locally recomputed digest.
    """
    from app.hashing import recompute_content_hash

    approved_hash = recompute_content_hash(APPROVED_BYTES)
    token, _claims = make_token(gate_decision_id=gate_decision, content_hash=approved_hash)

    response = _publish(
        client,
        agent_run,
        token,
        TAMPERED_BYTES,
        content_hash=approved_hash,
        asset_content_hash=approved_hash,
    )
    assert response.status_code == 403
    assert response.json()["detail"]["reason"] == "content_hash_mismatch"


def test_null_assets_content_hash_column_is_irrelevant(
    client, conn, agent_run, gate_decision, make_token
) -> None:
    """assets.content_hash is nullable text — Publisher must not read it
    via the PRIMARY bytes-recompute check (this test's own concern).

    NOTE: `asset_id` is deliberately NOT passed here (plan step 14):
    supplying it now triggers the NEW additional Vault cross-check
    (app/vault_lookup.py), which fails closed on a NULL content_hash --
    that behavior is covered by its own dedicated tests
    (test_agent_name_lookup_fails_closed.py). This test's own concern
    predates and is independent of that feature.
    """
    from app.hashing import recompute_content_hash

    conn.execute(
        """
        INSERT INTO assets (asset_type, agent_run_id, content_hash)
        VALUES ('social_post', %s, NULL) RETURNING id
        """,
        (agent_run,),
    ).fetchone()["id"]

    approved_hash = recompute_content_hash(APPROVED_BYTES)
    token, _claims = make_token(gate_decision_id=gate_decision, content_hash=approved_hash)

    # Correct bytes publish fine even though the column is NULL...
    ok = _publish(client, agent_run, token, APPROVED_BYTES)
    assert ok.status_code == 200

    # ...and a NULL column never rescues tampered bytes.
    token2, _ = make_token(gate_decision_id=gate_decision, content_hash=approved_hash)
    bad = _publish(client, agent_run, token2, TAMPERED_BYTES)
    assert bad.status_code == 403
    assert bad.json()["detail"]["reason"] == "content_hash_mismatch"
