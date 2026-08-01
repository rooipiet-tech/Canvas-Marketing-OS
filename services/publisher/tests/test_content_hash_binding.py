"""AC-07 — the NEW asset_id-based Vault cross-check (plan step 14,
PV3-01), additional to (never instead of) the existing primary
bytes-recompute check (test_verify_hash_mismatch.py, unchanged).

fetch_asset_and_agent_name is monkeypatched at the router module level —
there is no live Vault HTTP service in this test process; the isolated
unit tests for that function itself (against httpx.MockTransport) live in
test_vault_lookup.py.
"""

from __future__ import annotations

import base64

import app.routers.publish as publish_router
from app.vault_lookup import AssetLookupResult, VaultLookupError

APPROVED_BYTES = b"a client-free LinkedIn proof point post."


def _publish(client, agent_run, token, asset_bytes, asset_id=None):
    payload = {
        "agent_run_id": str(agent_run),
        "function_id": "publish.social_post",
        "asset_bytes_b64": base64.b64encode(asset_bytes).decode(),
        "gate_token": token,
    }
    if asset_id is not None:
        payload["asset_id"] = asset_id
    return client.post("/publish", json=payload)


def test_matching_asset_id_hash_proceeds(
    client, conn, agent_run, gate_decision, make_token, monkeypatch
) -> None:
    from app.hashing import recompute_content_hash

    approved_hash = recompute_content_hash(APPROVED_BYTES)
    token, _claims = make_token(gate_decision_id=gate_decision, content_hash=approved_hash)

    monkeypatch.setattr(
        publish_router,
        "fetch_asset_and_agent_name",
        lambda asset_id: AssetLookupResult(
            content_hash=approved_hash, agent_name="brand-steward-qa", agent_run_id=str(agent_run)
        ),
    )

    response = _publish(client, agent_run, token, APPROVED_BYTES, asset_id="asset-1")
    assert response.status_code == 200
    assert response.json()["reason"] == "published_dry_run"  # PUBLISHER_DRY_RUN defaults true


def test_mismatched_asset_id_hash_refuses(
    client, conn, agent_run, gate_decision, make_token, monkeypatch
) -> None:
    """Vault's OWN stored content_hash for asset_id disagrees with the
    recomputed bytes hash — an ADDITIONAL refusal case alongside (not
    replacing) the primary bytes-vs-token check."""
    from app.hashing import recompute_content_hash
    from app.vault_adapter import get_vault_adapter

    approved_hash = recompute_content_hash(APPROVED_BYTES)
    token, _claims = make_token(gate_decision_id=gate_decision, content_hash=approved_hash)

    monkeypatch.setattr(
        publish_router,
        "fetch_asset_and_agent_name",
        lambda asset_id: AssetLookupResult(
            content_hash="totally-different-hash-in-vault",
            agent_name="brand-steward-qa",
            agent_run_id=str(agent_run),
        ),
    )

    response = _publish(client, agent_run, token, APPROVED_BYTES, asset_id="asset-1")
    assert response.status_code == 403
    assert response.json()["detail"]["reason"] == "content_hash_mismatch"
    assert get_vault_adapter().call_count == 0


def test_asset_id_lookup_failure_fails_closed(
    client, conn, agent_run, gate_decision, make_token, monkeypatch
) -> None:
    from app.hashing import recompute_content_hash
    from app.vault_adapter import get_vault_adapter

    approved_hash = recompute_content_hash(APPROVED_BYTES)
    token, _claims = make_token(gate_decision_id=gate_decision, content_hash=approved_hash)

    def _raise(asset_id):
        raise VaultLookupError("simulated Vault outage")

    monkeypatch.setattr(publish_router, "fetch_asset_and_agent_name", _raise)

    response = _publish(client, agent_run, token, APPROVED_BYTES, asset_id="asset-1")
    assert response.status_code == 403
    assert response.json()["detail"]["reason"] == "vault_lookup_failed"
    assert get_vault_adapter().call_count == 0
