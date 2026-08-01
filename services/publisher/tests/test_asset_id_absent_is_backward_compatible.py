"""PV3-01 backward-compatibility contract: an existing caller that NEVER
sets asset_id (e.g. caj-governance-smoke's exact call shape) sees
byte-identical behavior to pre-session Publisher -- no Vault lookup is
attempted at all, ordinary PUBLISHER_DRY_RUN-driven behavior applies.
"""

from __future__ import annotations

import base64

import app.routers.publish as publish_router

ASSET_BYTES = b"caj-governance-smoke's exact real production-shaped payload."


def test_asset_id_absent_never_triggers_a_vault_lookup(
    client, conn, agent_run, gate_decision, make_token, monkeypatch
) -> None:
    from app.hashing import recompute_content_hash
    from app.vault_adapter import get_vault_adapter

    def _fail_if_called(asset_id):  # pragma: no cover
        raise AssertionError(
            "fetch_asset_and_agent_name must never be called when asset_id is absent"
        )

    monkeypatch.setattr(publish_router, "fetch_asset_and_agent_name", _fail_if_called)

    content_hash = recompute_content_hash(ASSET_BYTES)
    token, _claims = make_token(gate_decision_id=gate_decision, content_hash=content_hash)

    # No asset_id key at all -- exactly caj-governance-smoke's shape.
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
    assert get_vault_adapter().call_count == 1


def test_asset_id_explicit_none_is_also_treated_as_absent(
    client, conn, agent_run, gate_decision, make_token, monkeypatch
) -> None:
    from app.hashing import recompute_content_hash

    def _fail_if_called(asset_id):  # pragma: no cover
        raise AssertionError(
            "fetch_asset_and_agent_name must never be called when asset_id is None"
        )

    monkeypatch.setattr(publish_router, "fetch_asset_and_agent_name", _fail_if_called)

    content_hash = recompute_content_hash(ASSET_BYTES)
    token, _claims = make_token(gate_decision_id=gate_decision, content_hash=content_hash)

    response = client.post(
        "/publish",
        json={
            "agent_run_id": str(agent_run),
            "function_id": "publish.social_post",
            "asset_bytes_b64": base64.b64encode(ASSET_BYTES).decode(),
            "gate_token": token,
            "asset_id": None,
        },
    )
    assert response.status_code == 200
