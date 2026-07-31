"""PV3-01 fail-closed contract: asset_id SUPPLIED but the Vault lookup
errors/malformed -> Publisher refuses to publish (never proceeds as if
asset_id had been absent, never silently defaults to non-dry-run).
"""

from __future__ import annotations

import base64

import app.routers.publish as publish_router
from app.vault_lookup import VaultLookupError

ASSET_BYTES = b"a client-free LinkedIn proof point post for the fail-closed test."


def test_lookup_exception_refuses_and_never_calls_buffer_or_vault_adapter(
    client, conn, agent_run, gate_decision, make_token, monkeypatch
) -> None:
    from app.hashing import recompute_content_hash
    from app.vault_adapter import get_vault_adapter

    class _NeverCalledBufferClient:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            pass

        def list_queue_count(self, channel_id):  # pragma: no cover
            raise AssertionError("must never be called when the lookup fails closed")

        def create_draft(self, *, channel_id, text):  # pragma: no cover
            raise AssertionError("must never be called when the lookup fails closed")

    # Even in live mode, a broken lookup still refuses.
    monkeypatch.setenv("PUBLISHER_DRY_RUN", "false")
    monkeypatch.setattr(publish_router, "BufferClient", lambda base_url: _NeverCalledBufferClient())

    def _raise(asset_id):
        raise VaultLookupError("simulated malformed Vault response")

    monkeypatch.setattr(publish_router, "fetch_asset_and_agent_name", _raise)

    content_hash = recompute_content_hash(ASSET_BYTES)
    token, _claims = make_token(gate_decision_id=gate_decision, content_hash=content_hash)

    response = client.post(
        "/publish",
        json={
            "agent_run_id": str(agent_run),
            "function_id": "publish.social_post",
            "asset_bytes_b64": base64.b64encode(ASSET_BYTES).decode(),
            "gate_token": token,
            "asset_id": "broken-asset-1",
        },
    )
    assert response.status_code == 403
    assert response.json()["detail"]["reason"] == "vault_lookup_failed"
    assert get_vault_adapter().call_count == 0

    rows = conn.execute("SELECT outcome, reason FROM governance.publish_attempts").fetchall()
    assert len(rows) == 1
    assert rows[0]["outcome"] == "rejected"
    assert rows[0]["reason"] == "vault_lookup_failed"
