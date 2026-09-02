"""AC-08(e)/AC-30 — a request tagged as originating from the S8 proof
circuit (asset_id resolving to agent_name=='loop-proof-circuit') NEVER
reaches create_draft, even with PUBLISHER_DRY_RUN explicitly set to
false/live.
"""

from __future__ import annotations

import base64

import app.routers.publish as publish_router
from app.config import AGENT_NAME_LOOP_PROOF
from app.vault_lookup import AssetLookupResult

ASSET_BYTES = b"a client-free LinkedIn proof point post for the proof-circuit test."


class _NeverCalledBufferClient:
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        pass

    def list_queue_count(self, channel_id):  # pragma: no cover
        raise AssertionError("list_queue must never be called for a proof-circuit asset")

    def create_draft(
        self, *, channel_id, text, utm_campaign=None, post_archetype=None
    ):  # pragma: no cover
        raise AssertionError("create_draft must never be called for a proof-circuit asset")


def test_proof_circuit_asset_stays_dry_run_even_with_global_flag_false(
    client, conn, agent_run, gate_decision, make_token, monkeypatch
) -> None:
    from app.hashing import recompute_content_hash

    # Explicitly flipped to live/false -- the global flag alone would
    # normally call Buffer, but this asset is tagged loop-proof-circuit.
    monkeypatch.setenv("PUBLISHER_DRY_RUN", "false")
    monkeypatch.setattr(publish_router, "BufferClient", lambda base_url: _NeverCalledBufferClient())

    content_hash = recompute_content_hash(ASSET_BYTES)
    monkeypatch.setattr(
        publish_router,
        "fetch_asset_and_agent_name",
        lambda asset_id: AssetLookupResult(
            content_hash=content_hash, agent_name=AGENT_NAME_LOOP_PROOF, agent_run_id=str(agent_run)
        ),
    )

    token, _claims = make_token(gate_decision_id=gate_decision, content_hash=content_hash)
    response = client.post(
        "/publish",
        json={
            "agent_run_id": str(agent_run),
            "function_id": "publish.social_post",
            "asset_bytes_b64": base64.b64encode(ASSET_BYTES).decode(),
            "gate_token": token,
            "asset_id": "loop-proof-asset-1",
        },
    )
    assert response.status_code == 200
    assert response.json()["reason"] == "published_dry_run"


def test_non_proof_circuit_asset_honors_the_live_flag(
    client, conn, agent_run, gate_decision, make_token, monkeypatch
) -> None:
    """Sanity check the inverse: a NON-proof-circuit asset_id (agent_name
    is some ordinary drafting agent, not loop-proof-circuit) with
    PUBLISHER_DRY_RUN=false DOES reach create_draft."""
    from app.hashing import recompute_content_hash

    calls: list = []

    class _RecordingBufferClient:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            pass

        def list_queue_count(self, channel_id):
            return 0

        def create_draft(
            self, *, channel_id, text, utm_campaign=None, post_archetype=None
        ):
            calls.append((channel_id, text))
            return {"post": {"id": "p1", "status": "draft"}}

    monkeypatch.setenv("PUBLISHER_DRY_RUN", "false")
    monkeypatch.setattr(publish_router, "BufferClient", lambda base_url: _RecordingBufferClient())

    content_hash = recompute_content_hash(ASSET_BYTES)
    monkeypatch.setattr(
        publish_router,
        "fetch_asset_and_agent_name",
        lambda asset_id: AssetLookupResult(
            content_hash=content_hash,
            agent_name="linkedin-post-writer",
            agent_run_id=str(agent_run),
        ),
    )

    token, _claims = make_token(gate_decision_id=gate_decision, content_hash=content_hash)
    response = client.post(
        "/publish",
        json={
            "agent_run_id": str(agent_run),
            "function_id": "publish.social_post",
            "asset_bytes_b64": base64.b64encode(ASSET_BYTES).decode(),
            "gate_token": token,
            "asset_id": "ordinary-asset-1",
        },
    )
    assert response.status_code == 200
    assert response.json()["reason"] == "published"
    assert len(calls) == 1
