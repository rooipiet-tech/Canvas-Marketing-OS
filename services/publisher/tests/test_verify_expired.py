"""AC-08 — an expired gate token is refused with `token_expired`."""

from __future__ import annotations

import base64
import time

ASSET_BYTES = b"asset behind an expired token"


def test_rejects_expired_token(client, conn, agent_run, gate_decision, make_token) -> None:
    from app.hashing import recompute_content_hash
    from app.vault_adapter import get_vault_adapter

    content_hash = recompute_content_hash(ASSET_BYTES)
    now = int(time.time())
    token, claims = make_token(
        gate_decision_id=gate_decision,
        content_hash=content_hash,
        issued_at=now - 7200,
        ttl_seconds=3600,  # expired an hour ago
    )
    assert claims["exp"] < now

    response = client.post(
        "/publish",
        json={
            "agent_run_id": str(agent_run),
            "function_id": "publish.social_post",
            "asset_bytes_b64": base64.b64encode(ASSET_BYTES).decode(),
            "gate_token": token,
        },
    )
    assert response.status_code == 403

    body = response.json()["detail"]
    assert body["reason"] == "token_expired"
    assert body["reason"] not in {"token_absent", "content_hash_mismatch", "token_replayed"}

    rows = conn.execute("SELECT outcome, reason FROM governance.publish_attempts").fetchall()
    assert len(rows) == 1
    assert rows[0]["outcome"] == "rejected"
    assert rows[0]["reason"] == "token_expired"

    assert get_vault_adapter().call_count == 0

    # An expired token does NOT burn its jti — the refusal happens first.
    ledger = conn.execute(
        "SELECT count(*) AS n FROM governance.jti_ledger WHERE jti = %s", (claims["jti"],)
    ).fetchone()["n"]
    assert ledger == 0


def test_a_token_still_inside_its_window_is_accepted(
    client, conn, agent_run, gate_decision, make_token
) -> None:
    """Control case, so the expiry test cannot pass for the wrong reason."""
    from app.hashing import recompute_content_hash

    content_hash = recompute_content_hash(ASSET_BYTES)
    token, _claims = make_token(
        gate_decision_id=gate_decision, content_hash=content_hash, ttl_seconds=900
    )
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
    assert response.json()["outcome"] == "published"
