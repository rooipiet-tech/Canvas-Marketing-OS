"""POST /sign-option-card-link — internal-only signing endpoint
(Appendix D PR 5), driven against the INTERNAL app (main.py, not
approval_main.py)."""

from __future__ import annotations

import uuid

from app.option_link_sig import verify_card_link


def test_sign_option_card_link_returns_a_verifiable_signature(client) -> None:
    card_id = str(uuid.uuid4())
    resp = client.post("/sign-option-card-link", json={"card_id": card_id})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["card_id"] == card_id
    assert verify_card_link(card_id, body["sig"]) is True


def test_signature_does_not_verify_for_a_different_card(client) -> None:
    card_id = str(uuid.uuid4())
    other_card_id = str(uuid.uuid4())
    resp = client.post("/sign-option-card-link", json={"card_id": card_id})
    sig = resp.json()["sig"]
    assert verify_card_link(other_card_id, sig) is False


def test_missing_card_id_is_422(client) -> None:
    resp = client.post("/sign-option-card-link", json={})
    assert resp.status_code == 422
