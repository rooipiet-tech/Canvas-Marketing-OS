"""AC-17 — an internal agent caller gets full HTTP parity on Gatekeeper.

POST /gate-check then GET /decisions/{id} over the internal-only ingress,
with no human/Teams-only step required to observe the result.
"""

from __future__ import annotations


def test_gate_check_then_get_decision(client, conn, agent_run) -> None:
    posted = client.post(
        "/gate-check",
        json={
            "agent_run_id": str(agent_run),
            "function_id": "analyse.signal",
            "action_class": "analyse",
            "content_hash": "9" * 64,
        },
    )
    assert posted.status_code == 200
    decision = posted.json()
    assert decision["outcome"] == "approved"
    assert decision["gate_token"]

    fetched = client.get(f"/decisions/{decision['decision_id']}")
    assert fetched.status_code == 200
    body = fetched.json()

    assert body["id"] == decision["decision_id"]
    assert body["agent_run_id"] == str(agent_run)
    assert body["outcome"] == "approved"
    assert body["reason"] == decision["reason"]
    assert body["decided_at"]

    # An escalated (approval-required) decision is equally observable over
    # HTTP — no Teams round trip is needed to learn what happened.
    escalated = client.post(
        "/gate-check",
        json={
            "agent_run_id": str(agent_run),
            "function_id": "publish.social_post",
            "action_class": "publish",
            "content_hash": "8" * 64,
        },
    ).json()
    assert escalated["outcome"] == "escalated"
    readback = client.get(f"/decisions/{escalated['decision_id']}").json()
    assert readback["outcome"] == "escalated"
    assert "level_1_requires_approval" in readback["reason"]

    assert client.get("/decisions/00000000-0000-0000-0000-000000000000").status_code == 404
    assert client.get("/healthz").json()["status"] == "ok"


def _paths(test_client) -> set[str]:
    return set(test_client.get("/openapi.json").json()["paths"])


def test_internal_app_does_not_expose_the_approval_action_route(client) -> None:
    """Physical route separation: the external surface is a separate app."""
    paths = _paths(client)
    assert "/gate-check" in paths
    assert "/decisions/{decision_id}" in paths
    assert not any(path.startswith("/approval-action") for path in paths)


def test_approval_app_exposes_only_the_approval_action_route(approval_client) -> None:
    paths = _paths(approval_client)
    assert "/approval-action/{link_token}" in paths
    assert "/gate-check" not in paths
    assert not any(path.startswith("/decisions") for path in paths)
