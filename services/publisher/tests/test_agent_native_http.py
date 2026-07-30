"""AC-17 — an internal agent caller gets full HTTP parity on Publisher.

POST /publish then GET /publish-attempts/{id}, for both the success and
the refusal path — no human-only step anywhere in the loop.
"""

from __future__ import annotations

import base64

ASSET_BYTES = b"agent-native publish payload"


def test_publish_then_get_audit(client, conn, agent_run, gate_decision, make_token) -> None:
    from app.hashing import recompute_content_hash

    content_hash = recompute_content_hash(ASSET_BYTES)
    token, claims = make_token(gate_decision_id=gate_decision, content_hash=content_hash)

    posted = client.post(
        "/publish",
        json={
            "agent_run_id": str(agent_run),
            "function_id": "publish.social_post",
            "asset_bytes_b64": base64.b64encode(ASSET_BYTES).decode(),
            "gate_token": token,
        },
    )
    assert posted.status_code == 200
    body = posted.json()

    fetched = client.get(f"/publish-attempts/{body['attempt_id']}")
    assert fetched.status_code == 200
    record = fetched.json()

    assert record["id"] == body["attempt_id"]
    assert record["outcome"] == "published"
    assert record["reason"] == "published"
    assert record["content_hash"] == content_hash
    assert record["jti"] == claims["jti"]
    assert record["gate_decision_id"] == str(gate_decision)
    assert record["agent_run_id"] == str(agent_run)
    assert record["created_at"]

    # A refusal is equally observable over HTTP.
    refused = client.post(
        "/publish",
        json={
            "agent_run_id": str(agent_run),
            "function_id": "publish.social_post",
            "asset_bytes_b64": base64.b64encode(ASSET_BYTES).decode(),
        },
    )
    assert refused.status_code == 403
    refused_id = refused.json()["detail"]["attempt_id"]
    refused_record = client.get(f"/publish-attempts/{refused_id}").json()
    assert refused_record["outcome"] == "rejected"
    assert refused_record["reason"] == "token_absent"

    assert client.get("/publish-attempts/00000000-0000-0000-0000-000000000000").status_code == 404
    assert client.get("/publish-attempts/not-a-uuid").status_code == 400
    assert client.get("/healthz").json()["status"] == "ok"


def test_publisher_has_no_external_approval_surface(client) -> None:
    paths = set(client.get("/openapi.json").json()["paths"])
    assert paths == {"/publish", "/publish-attempts/{attempt_id}", "/healthz"}
