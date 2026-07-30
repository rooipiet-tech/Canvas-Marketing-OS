"""AC-18 — replay rejection survives across independent verifier objects.

Two verifier/ledger pairs are constructed completely independently and
share NOTHING except the database. If any replay state lived in process
memory, the second instance would happily accept the replayed token.
"""

from __future__ import annotations

import psycopg
from app.jti_ledger import JtiLedger
from app.verifier import GateTokenVerifier
from psycopg.rows import dict_row

ISSUER = "cmos-gatekeeper"
AUDIENCE = "cmos-publisher"


def test_replay_rejected_across_independent_verifier_instances(
    database_url, conn, gate_decision, make_token, signing_keys
) -> None:
    _private, public_pem = signing_keys
    token, claims = make_token(gate_decision_id=gate_decision, content_hash="c" * 64)

    # Two separate connections and two separate verifier objects — no
    # shared Python state whatsoever.
    conn_a = psycopg.connect(database_url, autocommit=True, row_factory=dict_row)
    conn_b = psycopg.connect(database_url, autocommit=True, row_factory=dict_row)
    try:
        verifier_a = GateTokenVerifier(
            public_key_pem=public_pem, issuer=ISSUER, audience=AUDIENCE
        )
        verifier_b = GateTokenVerifier(
            public_key_pem=public_pem, issuer=ISSUER, audience=AUDIENCE
        )
        assert verifier_a is not verifier_b

        ledger_a = JtiLedger(conn_a)
        ledger_b = JtiLedger(conn_b)
        assert ledger_a is not ledger_b

        # Instance A verifies and consumes.
        claims_a = verifier_a.verify(token)
        assert claims_a["jti"] == claims["jti"]
        assert ledger_a.consume(claims_a["jti"], claims_a["gate_decision_id"]) is True

        # Instance B — which has never seen this token in-process — still
        # rejects the replay, because the ledger is durable shared state.
        claims_b = verifier_b.verify(token)
        assert claims_b["jti"] == claims["jti"]
        assert ledger_b.consume(claims_b["jti"], claims_b["gate_decision_id"]) is False
        assert ledger_b.seen(claims["jti"]) is True

        # A third, freshly-connected instance agrees.
        conn_c = psycopg.connect(database_url, autocommit=True, row_factory=dict_row)
        try:
            assert JtiLedger(conn_c).consume(claims["jti"]) is False
        finally:
            conn_c.close()
    finally:
        conn_a.close()
        conn_b.close()

    row_count = conn.execute(
        "SELECT count(*) AS n FROM governance.jti_ledger WHERE jti = %s", (claims["jti"],)
    ).fetchone()["n"]
    assert row_count == 1
