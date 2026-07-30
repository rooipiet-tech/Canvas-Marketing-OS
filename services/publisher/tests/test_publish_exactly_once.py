"""AC-11 — a fully valid token publishes exactly once."""

from __future__ import annotations

import base64

ASSET_BYTES = b"a perfectly valid asset payload"


def test_valid_token_publishes_exactly_once(
    client, conn, agent_run, gate_decision, make_token
) -> None:
    from app.hashing import recompute_content_hash
    from app.vault_adapter import get_vault_adapter

    adapter = get_vault_adapter()
    assert adapter.call_count == 0

    content_hash = recompute_content_hash(ASSET_BYTES)
    token, claims = make_token(gate_decision_id=gate_decision, content_hash=content_hash)

    response = client.post(
        "/publish",
        json={
            "agent_run_id": str(agent_run),
            "function_id": "publish.social_post",
            "asset_bytes_b64": base64.b64encode(ASSET_BYTES).decode(),
            "gate_token": token,
        },
    )
    assert response.status_code == 200

    body = response.json()
    assert body["outcome"] == "published"
    assert body["reason"] == "published"
    assert body["content_hash"] == content_hash
    assert body["jti"] == claims["jti"]
    assert body["vault_record_id"]

    # Exactly one call to the stub Vault-recording adapter.
    assert adapter.call_count == 1
    recorded = adapter.calls[0]
    assert recorded.content_hash == content_hash
    assert recorded.function_id == "publish.social_post"
    assert recorded.gate_decision_id == str(gate_decision)
    assert recorded.jti == claims["jti"]
    assert recorded.record_id == body["vault_record_id"]

    # Exactly one publish_attempts row, outcome=published.
    rows = conn.execute(
        "SELECT id, outcome, reason, content_hash, jti FROM governance.publish_attempts"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["outcome"] == "published"
    assert rows[0]["content_hash"] == content_hash
    assert rows[0]["jti"] == claims["jti"]
    assert str(rows[0]["id"]) == body["attempt_id"]
