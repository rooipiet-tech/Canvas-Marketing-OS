"""GET /decide — option_cards / approval_decisions click semantics
(Appendix D PR 3).

Mirrors test_approval_action_auth.py's approach: drives the EXTERNAL
approval app (approval_main.py) with synthetic Easy-Auth headers, exactly
as Container Apps' built-in authentication would inject them.
"""

from __future__ import annotations

from app.option_link_sig import sign_card_link

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


def test_choosing_the_recommended_option_records_was_recommended_true(
    approval_client, conn, make_option_card
) -> None:
    card_id = make_option_card(recommended_option_id="A")
    sig = sign_card_link(str(card_id))

    resp = approval_client.get(
        "/decide", params={"card": str(card_id), "opt": "A", "sig": sig}, headers=PRINCIPAL_A
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["outcome"] == "chosen"
    assert body["chosen_option_id"] == "A"
    assert body["was_recommended"] is True
    assert "alice@cmos.example" in body["decided_by"]

    row = conn.execute(
        "SELECT * FROM approval_decisions WHERE card_id = %s", (str(card_id),)
    ).fetchone()
    assert row["outcome"] == "chosen"
    assert row["channel"] == "teams_card"
    assert row["signature"]


def test_choosing_a_non_recommended_option_records_was_recommended_false(
    approval_client, make_option_card
) -> None:
    card_id = make_option_card(recommended_option_id="A")
    sig = sign_card_link(str(card_id))

    resp = approval_client.get(
        "/decide", params={"card": str(card_id), "opt": "B", "sig": sig}, headers=PRINCIPAL_A
    )
    assert resp.status_code == 200
    assert resp.json()["was_recommended"] is False


def test_reject_all_defaults_rejection_code_to_other(
    approval_client, conn, make_option_card
) -> None:
    card_id = make_option_card()
    sig = sign_card_link(str(card_id))

    resp = approval_client.get(
        "/decide",
        params={"card": str(card_id), "outcome": "rejected_all", "sig": sig},
        headers=PRINCIPAL_A,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["outcome"] == "rejected_all"
    assert body["chosen_option_id"] is None
    assert body["rejection_code"] == "other"

    row = conn.execute(
        "SELECT rejection_code FROM approval_decisions WHERE card_id = %s", (str(card_id),)
    ).fetchone()
    assert row["rejection_code"] == "other"


def test_decider_is_the_authenticated_principal_not_a_link_claim(
    approval_client, make_option_card
) -> None:
    card_id = make_option_card()
    sig = sign_card_link(str(card_id))

    resp = approval_client.get(
        "/decide", params={"card": str(card_id), "opt": "A", "sig": sig}, headers=PRINCIPAL_B
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "bob@cmos.example" in body["decided_by"]
    assert "alice@cmos.example" not in body["decided_by"]


def test_no_authenticated_principal_is_401(approval_client, make_option_card) -> None:
    card_id = make_option_card()
    sig = sign_card_link(str(card_id))

    resp = approval_client.get("/decide", params={"card": str(card_id), "opt": "A", "sig": sig})
    assert resp.status_code == 401


def test_wrong_signature_is_403(approval_client, make_option_card) -> None:
    card_id = make_option_card()

    resp = approval_client.get(
        "/decide",
        params={"card": str(card_id), "opt": "A", "sig": "not-a-real-signature"},
        headers=PRINCIPAL_A,
    )
    assert resp.status_code == 403


def test_signature_for_a_different_card_is_403(approval_client, make_option_card) -> None:
    card_id = make_option_card()
    other_card_id = make_option_card()
    sig_for_other_card = sign_card_link(str(other_card_id))

    resp = approval_client.get(
        "/decide",
        params={"card": str(card_id), "opt": "A", "sig": sig_for_other_card},
        headers=PRINCIPAL_A,
    )
    assert resp.status_code == 403


def test_unknown_card_is_404(approval_client) -> None:
    fake_card_id = "99999999-9999-9999-9999-999999999999"
    sig = sign_card_link(fake_card_id)

    resp = approval_client.get(
        "/decide", params={"card": fake_card_id, "opt": "A", "sig": sig}, headers=PRINCIPAL_A
    )
    assert resp.status_code == 404


def test_expired_card_is_410_and_records_no_decision(
    approval_client, conn, make_option_card
) -> None:
    card_id = make_option_card(expires_in_hours=-1)
    sig = sign_card_link(str(card_id))

    resp = approval_client.get(
        "/decide", params={"card": str(card_id), "opt": "A", "sig": sig}, headers=PRINCIPAL_A
    )
    assert resp.status_code == 410

    row = conn.execute(
        "SELECT * FROM approval_decisions WHERE card_id = %s", (str(card_id),)
    ).fetchone()
    assert row is None


def test_option_not_on_the_card_is_400(approval_client, make_option_card) -> None:
    card_id = make_option_card(option_ids=("A", "B"))
    sig = sign_card_link(str(card_id))

    resp = approval_client.get(
        "/decide", params={"card": str(card_id), "opt": "C", "sig": sig}, headers=PRINCIPAL_A
    )
    assert resp.status_code == 400


def test_a_second_decision_on_the_same_card_is_409(approval_client, make_option_card) -> None:
    card_id = make_option_card()
    sig = sign_card_link(str(card_id))

    first = approval_client.get(
        "/decide", params={"card": str(card_id), "opt": "A", "sig": sig}, headers=PRINCIPAL_A
    )
    assert first.status_code == 200

    second = approval_client.get(
        "/decide", params={"card": str(card_id), "opt": "B", "sig": sig}, headers=PRINCIPAL_B
    )
    assert second.status_code == 409


def test_a_second_decision_does_not_overwrite_the_first(
    approval_client, conn, make_option_card
) -> None:
    card_id = make_option_card()
    sig = sign_card_link(str(card_id))

    approval_client.get(
        "/decide", params={"card": str(card_id), "opt": "A", "sig": sig}, headers=PRINCIPAL_A
    )
    approval_client.get(
        "/decide", params={"card": str(card_id), "opt": "B", "sig": sig}, headers=PRINCIPAL_B
    )

    rows = conn.execute(
        "SELECT * FROM approval_decisions WHERE card_id = %s", (str(card_id),)
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["chosen_option_id"] == "A"
    assert "alice@cmos.example" in rows[0]["decided_by"]


def test_missing_opt_and_outcome_is_400(approval_client, make_option_card) -> None:
    card_id = make_option_card()
    sig = sign_card_link(str(card_id))

    resp = approval_client.get(
        "/decide", params={"card": str(card_id), "sig": sig}, headers=PRINCIPAL_A
    )
    assert resp.status_code == 400


def test_both_opt_and_outcome_is_400(approval_client, make_option_card) -> None:
    card_id = make_option_card()
    sig = sign_card_link(str(card_id))

    resp = approval_client.get(
        "/decide",
        params={"card": str(card_id), "opt": "A", "outcome": "rejected_all", "sig": sig},
        headers=PRINCIPAL_A,
    )
    assert resp.status_code == 400
