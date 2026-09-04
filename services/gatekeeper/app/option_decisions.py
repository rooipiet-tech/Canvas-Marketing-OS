"""Postgres access for public.option_cards / public.approval_decisions
(services/vault/migrations/0002_options_inbox_init.sql +
0003_approval_decisions_add_channel.sql, Appendix D PR 1 / PR 3).

Mirrors app/routers/decisions.py's insert_gate_decision/fetch_gate_decision
pattern — parameterised SQL, dict rows, no ORM.
"""

from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime, timezone
from typing import Any

import psycopg

from app.signer import get_signer

# contracts/approval-decision.schema.json's own outcome enum.
VALID_OUTCOMES = {"chosen", "rejected_all", "deferred", "timeout_default", "expired_unresolved"}

# Must stay byte-identical in spirit to app/tokens.py's own convention:
# sorted keys, no whitespace, so the signature is reproducible.
CANONICAL_JSON_SEPARATORS = (",", ":")


class DecisionAlreadyRecorded(Exception):
    """card_id already has a decision (approval_decisions_one_per_card)."""

    def __init__(self, card_id: str) -> None:
        super().__init__(f"card {card_id} already has a decision")
        self.card_id = card_id


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def build_decision_signature(
    *, card_id: str, outcome: str, chosen_option_id: str | None, decided_at: str
) -> str:
    """contracts/approval-decision.schema.json's own description for
    `signature`: 'Key Vault signature over (card_id, outcome,
    chosen_option_id, decided_at) - same replay-rejection scheme the
    gatekeeper already uses.'"""
    payload = json.dumps(
        {
            "card_id": card_id,
            "outcome": outcome,
            "chosen_option_id": chosen_option_id,
            "decided_at": decided_at,
        },
        sort_keys=True,
        separators=CANONICAL_JSON_SEPARATORS,
    )
    return _b64url(get_signer().sign(payload.encode("utf-8")))


def fetch_card(conn, card_id: str | uuid.UUID) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM option_cards WHERE card_id = %s", (str(card_id),)).fetchone()
    return dict(row) if row else None


def fetch_decision(conn, card_id: str | uuid.UUID) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM approval_decisions WHERE card_id = %s", (str(card_id),)
    ).fetchone()
    return dict(row) if row else None


def record_decision(
    conn,
    *,
    card: dict[str, Any],
    outcome: str,
    chosen_option_id: str | None,
    rejection_code: str | None,
    decided_by: str,
    channel: str,
    latency_seconds: int | None = None,
) -> dict[str, Any]:
    """Insert one approval_decisions row.

    Relies on the table's own UNIQUE(card_id) constraint
    (approval_decisions_one_per_card) to reject a second decision on the
    same card atomically under concurrent requests — the equivalent of
    approval_action.py's conditional `link_consumed_at` UPDATE for the
    older gate_decisions flow, done here via the DB constraint instead
    since an option card carries no separate consumable "link" row of its
    own to race on.

    `card` is a full option_cards row (app/option_decisions.fetch_card's
    return value) — `card["card"]` is the OptionCard document itself
    (contracts/option-card.schema.json's shape), where
    `recommended_option_id` actually lives.
    """
    if outcome not in VALID_OUTCOMES:
        raise ValueError(f"outcome must be one of {sorted(VALID_OUTCOMES)}, got {outcome!r}")

    decided_at = datetime.now(timezone.utc).isoformat()
    recommended_option_id = card["card"].get("recommended_option_id")
    was_recommended = (
        chosen_option_id == recommended_option_id if outcome == "chosen" else None
    )
    signature = build_decision_signature(
        card_id=str(card["card_id"]),
        outcome=outcome,
        chosen_option_id=chosen_option_id,
        decided_at=decided_at,
    )

    try:
        row = conn.execute(
            """
            INSERT INTO approval_decisions
                (card_id, outcome, chosen_option_id, was_recommended, rejection_code,
                 decided_by, decided_at, latency_seconds, signature, channel)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (
                str(card["card_id"]),
                outcome,
                chosen_option_id,
                was_recommended,
                rejection_code,
                decided_by,
                decided_at,
                latency_seconds,
                signature,
                channel,
            ),
        ).fetchone()
    except psycopg.errors.UniqueViolation:
        raise DecisionAlreadyRecorded(str(card["card_id"])) from None
    return dict(row)
