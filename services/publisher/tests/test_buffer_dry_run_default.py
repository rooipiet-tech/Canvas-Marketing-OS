"""AC-08(a) — dry-run is the DEFAULT: with PUBLISHER_DRY_RUN unset (or
explicitly true), no HTTP call ever reaches mcp-buffer's create_draft;
instead a distinctly-recorded 'published_dry_run' reason is produced.
"""

from __future__ import annotations

import base64

import app.routers.publish as publish_router

ASSET_BYTES = b"a client-free LinkedIn proof point post for the dry-run default test."


class _NeverCalledBufferClient:
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        pass

    def list_queue_count(self, channel_id):  # pragma: no cover
        raise AssertionError("list_queue must never be called in dry-run mode")

    def create_draft(
        self, *, channel_id, text, utm_campaign=None, post_archetype=None
    ):  # pragma: no cover
        raise AssertionError("create_draft must never be called in dry-run mode")


def _publish(client, agent_run, token):
    return client.post(
        "/publish",
        json={
            "agent_run_id": str(agent_run),
            "function_id": "publish.social_post",
            "asset_bytes_b64": base64.b64encode(ASSET_BYTES).decode(),
            "gate_token": token,
        },
    )


def test_dry_run_is_the_default(
    client, conn, agent_run, gate_decision, make_token, monkeypatch
) -> None:
    from app.hashing import recompute_content_hash
    from app.vault_adapter import get_vault_adapter

    monkeypatch.delenv("PUBLISHER_DRY_RUN", raising=False)
    monkeypatch.setattr(publish_router, "BufferClient", lambda base_url: _NeverCalledBufferClient())

    content_hash = recompute_content_hash(ASSET_BYTES)
    token, _claims = make_token(gate_decision_id=gate_decision, content_hash=content_hash)

    response = _publish(client, agent_run, token)
    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "published"
    assert body["reason"] == "published_dry_run"
    # The Vault-recording adapter still fires -- dry-run only skips the
    # live Buffer call, never the existing internal audit adapter.
    assert get_vault_adapter().call_count == 1


def test_explicit_dry_run_true_behaves_identically(
    client, conn, agent_run, gate_decision, make_token, monkeypatch
) -> None:
    from app.hashing import recompute_content_hash

    monkeypatch.setenv("PUBLISHER_DRY_RUN", "true")
    monkeypatch.setattr(publish_router, "BufferClient", lambda base_url: _NeverCalledBufferClient())

    content_hash = recompute_content_hash(ASSET_BYTES)
    token, _claims = make_token(gate_decision_id=gate_decision, content_hash=content_hash)

    response = _publish(client, agent_run, token)
    assert response.status_code == 200
    assert response.json()["reason"] == "published_dry_run"
