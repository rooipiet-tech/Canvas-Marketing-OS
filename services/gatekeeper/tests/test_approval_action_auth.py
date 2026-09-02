"""AC-32, AC-33, AC-34 — approval-link click semantics.

Every test here drives the EXTERNAL approval app (approval_main.py) with
synthetic Easy-Auth headers, exactly as Container Apps' built-in
authentication would inject them.
"""

from __future__ import annotations

import base64
import json

import pytest
from app.approval_inbox import create_approval_request
from app.config import APPROVAL_LINK_TTL_SECONDS

PRINCIPAL_A = {
    "X-MS-CLIENT-PRINCIPAL-ID": "11111111-1111-1111-1111-111111111111",
    "X-MS-CLIENT-PRINCIPAL-NAME": "alice@cmos.example",
    "X-MS-CLIENT-PRINCIPAL-IDP": "aad",
}
PRINCIPAL_B = {
    "X-MS-CLIENT-PRINCIPAL-ID": "22222222-2222-2222-2222-222222222222",
    "X-MS-CLIENT-PRINCIPAL-NAME": "bob@cmos.example",
    "X-MS-CLIENT-PRINCIPAL-IDP": "aad",
}


def _make_link(conn, agent_run, *, ttl_seconds: int | None = None) -> str:
    approval = create_approval_request(
        conn,
        gate_decision_id=None,
        agent_run_id=agent_run,
        function_id="publish.social_post",
        action_class="publish",
        level=1,
        content_hash="1" * 64,
        preview_title="Preview title",
        preview_reference="asset://preview.png",
        evidence_summary="Evidence summary line.",
        ttl_seconds=ttl_seconds,
    )
    return approval["link_token"]


def _audit_rows(conn):
    return conn.execute(
        "SELECT * FROM governance.approval_actions ORDER BY created_at, id"
    ).fetchall()


def test_approver_identity_comes_from_authenticated_principal_not_link(
    approval_client, conn, agent_run, make_agent_run
) -> None:
    # Principal A approves link 1.
    token_a = _make_link(conn, agent_run)
    response_a = approval_client.get(
        f"/approval-action/{token_a}", params={"choice": "approve"}, headers=PRINCIPAL_A
    )
    assert response_a.status_code == 200
    body_a = response_a.json()
    assert body_a["outcome"] == "approved"
    assert "alice@cmos.example" in body_a["decided_by"]
    assert PRINCIPAL_A["X-MS-CLIENT-PRINCIPAL-ID"] in body_a["decided_by"]

    decision_a = conn.execute(
        "SELECT decided_by FROM gate_decisions WHERE id = %s", (body_a["decision_id"],)
    ).fetchone()
    assert "alice@cmos.example" in decision_a["decided_by"]
    assert "bob@cmos.example" not in decision_a["decided_by"]

    # A fresh, separate request: different link, different injected
    # principal. Identity must not leak across requests.
    other_run = make_agent_run()
    token_b = _make_link(conn, other_run)
    response_b = approval_client.get(
        f"/approval-action/{token_b}", params={"choice": "approve"}, headers=PRINCIPAL_B
    )
    assert response_b.status_code == 200
    body_b = response_b.json()
    assert "bob@cmos.example" in body_b["decided_by"]
    assert "alice@cmos.example" not in body_b["decided_by"]

    decision_b = conn.execute(
        "SELECT decided_by FROM gate_decisions WHERE id = %s", (body_b["decision_id"],)
    ).fetchone()
    assert "bob@cmos.example" in decision_b["decided_by"]
    assert "alice@cmos.example" not in decision_b["decided_by"]

    # And the inbox rows record the per-request approver too.
    inbox = {
        row["link_token"]: row["decided_by"]
        for row in conn.execute("SELECT link_token, decided_by FROM governance.approval_inbox")
    }
    assert "alice@cmos.example" in inbox[token_a]
    assert "bob@cmos.example" in inbox[token_b]


def test_unauthenticated_click_is_rejected(approval_client, conn, agent_run) -> None:
    """Possession of the link alone must never approve anything."""
    token = _make_link(conn, agent_run)
    response = approval_client.get(f"/approval-action/{token}", params={"choice": "approve"})
    assert response.status_code == 401

    row = conn.execute(
        "SELECT status, link_consumed_at FROM governance.approval_inbox WHERE link_token = %s",
        (token,),
    ).fetchone()
    assert row["status"] == "pending"
    assert row["link_consumed_at"] is None


def test_principal_can_come_from_easy_auth_claims_blob(
    approval_client, conn, agent_run
) -> None:
    """Some Easy Auth configurations inject only the base64 claims blob."""
    token = _make_link(conn, agent_run)
    blob = {
        "auth_typ": "aad",
        "claims": [
            {
                "typ": "http://schemas.microsoft.com/identity/claims/objectidentifier",
                "val": "33333333-3333-3333-3333-333333333333",
            },
            {"typ": "preferred_username", "val": "carol@cmos.example"},
        ],
    }
    encoded = base64.b64encode(json.dumps(blob).encode()).decode()
    response = approval_client.get(
        f"/approval-action/{token}",
        params={"choice": "reject"},
        headers={"X-MS-CLIENT-PRINCIPAL": encoded},
    )
    assert response.status_code == 200
    assert "carol@cmos.example" in response.json()["decided_by"]


def test_link_single_use_rejects_second_visit(approval_client, conn, agent_run) -> None:
    token = _make_link(conn, agent_run)

    first = approval_client.get(
        f"/approval-action/{token}", params={"choice": "approve"}, headers=PRINCIPAL_A
    )
    assert first.status_code == 200
    assert first.json()["outcome"] == "approved"

    second = approval_client.get(
        f"/approval-action/{token}", params={"choice": "approve"}, headers=PRINCIPAL_A
    )
    assert second.status_code == 409
    assert second.json()["detail"]["reason"] == "link_already_used"

    # Even a different principal cannot re-use a consumed link.
    third = approval_client.get(
        f"/approval-action/{token}", params={"choice": "reject"}, headers=PRINCIPAL_B
    )
    assert third.status_code == 409
    assert third.json()["detail"]["reason"] == "link_already_used"

    # Exactly one gate decision came out of the link, ever.
    decisions = conn.execute(
        "SELECT id FROM gate_decisions WHERE agent_run_id = %s", (agent_run,)
    ).fetchall()
    assert len(decisions) == 1


def test_link_expires_at_the_configured_ttl(approval_client, conn, agent_run) -> None:
    # The default TTL became 30 days on 6 Aug 2026 (Pieter's explicit
    # instruction, recorded with its trade-off at app/config.py's
    # APPROVAL_LINK_TTL_SECONDS); this test and its sibling in
    # test_teams_webhook.py both still asserted the old 24 hours and had
    # been failing ever since. Nothing caught it because no CI job ran
    # this suite. Asserted against the constant rather than a literal, so
    # the next change to the window has to come here deliberately.
    fresh = _make_link(conn, agent_run)
    remaining = conn.execute(
        "SELECT expires_at - created_at AS ttl FROM governance.approval_inbox "
        "WHERE link_token = %s",
        (fresh,),
    ).fetchone()["ttl"]
    assert remaining.total_seconds() <= APPROVAL_LINK_TTL_SECONDS

    # ...and a visit after 24h+ has elapsed is rejected.
    expired = _make_link(conn, agent_run)
    conn.execute(
        "UPDATE governance.approval_inbox SET expires_at = created_at - interval '1 second', "
        "created_at = now() - interval '25 hours' WHERE link_token = %s",
        (expired,),
    )
    response = approval_client.get(
        f"/approval-action/{expired}", params={"choice": "approve"}, headers=PRINCIPAL_A
    )
    assert response.status_code == 410
    assert response.json()["detail"]["reason"] == "link_expired"

    row = conn.execute(
        "SELECT status, link_consumed_at FROM governance.approval_inbox WHERE link_token = %s",
        (expired,),
    ).fetchone()
    assert row["status"] == "expired"
    assert row["link_consumed_at"] is None


@pytest.mark.parametrize(
    "case,expected_status,expected_outcome,expected_reason",
    [
        ("approve", 200, "approved", "approval_link_approved"),
        ("reject", 200, "rejected", "approval_link_rejected"),
        ("expired-link", 410, "link_expired", "link_expired"),
        ("replayed-link", 409, "link_already_used", "link_already_used"),
    ],
)
def test_all_four_click_outcomes_are_audited(
    approval_client,
    conn,
    agent_run,
    case,
    expected_status,
    expected_outcome,
    expected_reason,
) -> None:
    token = _make_link(conn, agent_run)

    if case == "expired-link":
        conn.execute(
            "UPDATE governance.approval_inbox SET expires_at = now() - interval '1 second' "
            "WHERE link_token = %s",
            (token,),
        )
    elif case == "replayed-link":
        # Consume it once first; that first click's audit row is the
        # baseline, and the click under test is the second visit.
        approval_client.get(
            f"/approval-action/{token}", params={"choice": "approve"}, headers=PRINCIPAL_A
        )

    before = len(_audit_rows(conn))

    choice = "reject" if case == "reject" else "approve"
    response = approval_client.get(
        f"/approval-action/{token}", params={"choice": choice}, headers=PRINCIPAL_A
    )
    assert response.status_code == expected_status

    rows = _audit_rows(conn)
    assert len(rows) - before == 1, f"{case}: expected exactly one new audit row"

    new_row = rows[-1]
    assert new_row["outcome"] == expected_outcome
    assert new_row["reason"] == expected_reason
    assert new_row["principal_id"] == PRINCIPAL_A["X-MS-CLIENT-PRINCIPAL-ID"]
    assert new_row["principal_name"] == PRINCIPAL_A["X-MS-CLIENT-PRINCIPAL-NAME"]

    # The four reasons are mutually distinguishable.
    assert expected_reason in {
        "approval_link_approved",
        "approval_link_rejected",
        "link_expired",
        "link_already_used",
    }
