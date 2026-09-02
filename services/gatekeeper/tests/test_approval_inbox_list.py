"""GET /approval-inbox — the route the console has always called and this
service never exposed (INTEG-002).

console/app/clients/gatekeeper_real.py calls GET /approval-inbox; the
internal app only ever registered /gate-check, /decisions/{id},
/approval-status and /approval-action/{token}. Because the console is
pinned to GATEKEEPER_API_MODE 'mock', whose inbox starts empty and is
filled only by test seeding, /approvals reported "no approvals pending"
indefinitely while real rows accumulated here and runs blocked behind
them — the worst shape a governance screen can fail in, since nobody
investigates a page that confidently says nothing is waiting.
"""

from __future__ import annotations

import pytest
from app.approval_inbox import STATUS_APPROVED, consume_link, create_approval_request


def _seed(conn, agent_run, *, preview_title: str, evidence: str) -> dict:
    return create_approval_request(
        conn,
        gate_decision_id=None,
        agent_run_id=agent_run,
        function_id="publish.social_post",
        action_class="publish",
        level=1,
        content_hash="a" * 64,
        preview_title=preview_title,
        preview_reference="weekly-content-loop://draft-newsletter",
        evidence_summary=evidence,
    )


def test_pending_approval_is_listed_with_its_evidence(conn, agent_run, client) -> None:
    """The whole point of the screen: an approver must be able to see
    what a claim rests on, or they can click but not disagree."""
    _seed(
        conn,
        agent_run,
        preview_title="Owned-channel newsletter — Fabric-native — fabric-native",
        evidence="Draft: One governed source of truth.\nProof points (1): ...",
    )

    response = client.get("/approval-inbox")

    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 1
    assert rows[0]["preview_title"].startswith("Owned-channel newsletter")
    assert "Proof points" in rows[0]["evidence_summary"]
    assert rows[0]["status"] == "pending"
    assert rows[0]["content_hash"] == "a" * 64


def test_link_token_is_never_returned(conn, agent_run, client) -> None:
    """link_token is the single-use secret inside the Approve/Reject deep
    link — anyone holding it can decide the approval. A list view shows
    what is pending; it never hands out the means to approve it."""
    approval = _seed(conn, agent_run, preview_title="Carousel", evidence="Evidence.")

    body = client.get("/approval-inbox").text

    assert approval["link_token"] not in body
    assert "link_token" not in body


def test_newest_first(conn, agent_run, client) -> None:
    _seed(conn, agent_run, preview_title="First raised", evidence="e")
    _seed(conn, agent_run, preview_title="Second raised", evidence="e")

    titles = [row["preview_title"] for row in client.get("/approval-inbox").json()]

    assert titles == ["Second raised", "First raised"]


def test_status_filter_narrows_and_a_decided_row_reports_its_decider(
    conn, agent_run, client
) -> None:
    """An operator asking "did that get approved, and by whom" is the
    same screen's job as "what is waiting"."""
    pending = _seed(conn, agent_run, preview_title="Still waiting", evidence="e")
    decided = _seed(conn, agent_run, preview_title="Already decided", evidence="e")
    consume_link(
        conn,
        decided["id"],
        status=STATUS_APPROVED,
        decided_by="alice@cmos.example",
    )

    pending_rows = client.get("/approval-inbox", params={"status": "pending"}).json()
    approved_rows = client.get("/approval-inbox", params={"status": "approved"}).json()

    assert [row["id"] for row in pending_rows] == [str(pending["id"])]
    assert [row["id"] for row in approved_rows] == [str(decided["id"])]
    assert approved_rows[0]["decided_by"] == "alice@cmos.example"
    assert approved_rows[0]["decided_at"] is not None


def test_unknown_status_is_rejected(client) -> None:
    """Default-deny on the filter rather than silently returning
    everything, which would read as "nothing matches that status"."""
    response = client.get("/approval-inbox", params={"status": "probably-fine"})

    assert response.status_code == 400
    assert "status must be one of" in response.json()["detail"]


@pytest.mark.parametrize("limit", [0, 201])
def test_limit_is_bounded(client, limit) -> None:
    assert client.get("/approval-inbox", params={"limit": limit}).status_code == 422


def test_empty_inbox_is_an_empty_list_not_an_error(client) -> None:
    """An empty inbox and a broken inbox must not look the same to the
    console — which is exactly the confusion this endpoint's absence
    created."""
    response = client.get("/approval-inbox")

    assert response.status_code == 200
    assert response.json() == []
