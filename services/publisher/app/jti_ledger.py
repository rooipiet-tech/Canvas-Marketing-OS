"""Durable single-use gate-token (jti) ledger (AC-10, AC-18).

DURABILITY IS THE POINT. Container Apps scales replicas out and restarts
them, so an in-process `seen_jti` set would happily accept a replayed
token on a different replica — or on the same replica after a restart.
The ledger is therefore a Postgres table whose PRIMARY KEY does the work:

    INSERT INTO governance.jti_ledger (jti, ...) VALUES (...)
    ON CONFLICT (jti) DO NOTHING
    RETURNING jti

A returned row means "this process just claimed the jti"; no row means
"someone already consumed it" — a replay. The claim is atomic, so two
concurrent publish attempts with the same token cannot both win.

This module holds NO module-level state and NO cache: everything it knows
comes from the connection handed to it.
"""

from __future__ import annotations

import uuid
from typing import Any

_CONSUME_JTI = """
    INSERT INTO governance.jti_ledger (jti, gate_decision_id)
    VALUES (%(jti)s, %(gate_decision_id)s)
    ON CONFLICT (jti) DO NOTHING
    RETURNING jti, gate_decision_id, consumed_at
"""

_SELECT_JTI = """
    SELECT jti, gate_decision_id, consumed_at
      FROM governance.jti_ledger
     WHERE jti = %(jti)s
"""


class JtiLedger:
    """Postgres-backed replay ledger. State lives entirely in the database."""

    def __init__(self, conn) -> None:
        self._conn = conn

    def consume(self, jti: str, gate_decision_id: str | uuid.UUID | None = None) -> bool:
        """Claim `jti` exactly once. Returns False when already consumed."""
        if not jti:
            raise ValueError("jti must be a non-empty string")
        with self._conn.cursor() as cur:
            cur.execute(
                _CONSUME_JTI,
                {
                    "jti": jti,
                    "gate_decision_id": str(gate_decision_id) if gate_decision_id else None,
                },
            )
            return cur.fetchone() is not None

    def seen(self, jti: str) -> bool:
        with self._conn.cursor() as cur:
            cur.execute(_SELECT_JTI, {"jti": jti})
            return cur.fetchone() is not None

    def get(self, jti: str) -> dict[str, Any] | None:
        with self._conn.cursor() as cur:
            cur.execute(_SELECT_JTI, {"jti": jti})
            row = cur.fetchone()
        return dict(row) if row else None


def consume_jti(conn, jti: str, gate_decision_id: str | uuid.UUID | None = None) -> bool:
    """Functional shorthand for JtiLedger(conn).consume(...)."""
    return JtiLedger(conn).consume(jti, gate_decision_id)
