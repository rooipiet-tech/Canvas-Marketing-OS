"""AC-07 — a publish with no gate token is refused with `token_absent`."""

from __future__ import annotations

import base64

import pytest

ASSET_BYTES = b"asset with no token"


@pytest.mark.parametrize(
    "token_value,label",
    [(None, "omitted"), ("", "empty-string"), ("   ", "whitespace-only")],
)
def test_rejects_missing_token(client, conn, agent_run, token_value, label) -> None:
    payload = {
        "agent_run_id": str(agent_run),
        "function_id": "publish.social_post",
        "asset_bytes_b64": base64.b64encode(ASSET_BYTES).decode(),
    }
    if token_value is not None:
        payload["gate_token"] = token_value

    response = client.post("/publish", json=payload)
    assert response.status_code == 403

    body = response.json()["detail"]
    assert body["outcome"] == "rejected"
    assert body["reason"] == "token_absent"

    rows = conn.execute(
        "SELECT id, outcome, reason FROM governance.publish_attempts"
    ).fetchall()
    assert len(rows) == 1, f"{label}: expected exactly one publish_attempts row"
    assert rows[0]["outcome"] == "rejected"
    assert rows[0]["reason"] == "token_absent"
    assert str(rows[0]["id"]) == body["attempt_id"]

    # `token_absent` is a distinct reason, not a generic failure.
    assert body["reason"] not in {"token_expired", "content_hash_mismatch", "token_replayed"}

    # Nothing was published.
    from app.vault_adapter import get_vault_adapter

    assert get_vault_adapter().call_count == 0
