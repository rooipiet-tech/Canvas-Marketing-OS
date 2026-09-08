"""AC-06 — gate tokens match the v2 gate-token contract (TD-14 v2 contract
window): function_id and content_hash are first-class, required top-level
claims, not packed into a canonical-JSON `resource` string. Gatekeeper is
the sole gate-token issuer in this system and now issues ONLY v2 tokens
(see app/tokens.py's module docstring) — Publisher's dual-accept
verification of a still-possible v1 token in flight is covered separately
by services/publisher/tests/test_verify_v2_gate_token.py and
test_verify_alg_pinning.py, not here.

Pure-unit: no database needed.
"""

from __future__ import annotations

import base64
import json
import uuid
from pathlib import Path

import pytest
from app.signer.local_signer import LocalRSASigner
from app.tokens import build_claims, issue_gate_token
from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[3]
GATE_TOKEN_V1_SCHEMA = REPO_ROOT / "contracts" / "gate-token" / "schema.json"
GATE_TOKEN_V2_SCHEMA = REPO_ROOT / "contracts" / "gate-token" / "v2" / "schema.json"

CONTENT_HASH = "b" * 64
FUNCTION_ID = "publish.social_post"


def _decode_segment(segment: str) -> dict:
    padding = "=" * (-len(segment) % 4)
    return json.loads(base64.urlsafe_b64decode(segment + padding))


def _issue(*, content_hash: str = CONTENT_HASH) -> tuple[str, dict]:
    signer = LocalRSASigner()
    return issue_gate_token(
        signer,
        gate_decision_id=str(uuid.uuid4()),
        subject=str(uuid.uuid4()),
        content_hash=content_hash,
        function_id=FUNCTION_ID,
        ttl_seconds=900,
    )


def test_issued_token_matches_v2_schema() -> None:
    token, claims = _issue()

    schema = json.loads(GATE_TOKEN_V2_SCHEMA.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)

    # The claim set as issued validates against v2...
    validator.validate(claims)
    # ...and so does what actually went out on the wire.
    header_segment, payload_segment, signature_segment = token.split(".")
    on_the_wire = _decode_segment(payload_segment)
    validator.validate(on_the_wire)
    assert on_the_wire == claims

    # additionalProperties:false is honoured — no invented top-level claim.
    assert set(claims) <= set(schema["properties"])
    assert set(schema["required"]) <= set(claims)

    # v2: function_id/content_hash are first-class top-level claims...
    assert claims["function_id"] == FUNCTION_ID
    assert claims["content_hash"] == CONTENT_HASH
    # ...and no resource claim is built at all — v1's packed-string form is
    # retired on the issuing side (app/tokens.py's module docstring).
    assert "resource" not in claims

    # RS256 only (this Key Vault SKU cannot do EdDSA).
    assert _decode_segment(header_segment)["alg"] == "RS256"
    assert signature_segment

    # Sanity: a schema-invalid extra claim really would be caught.
    with pytest.raises(Exception):
        validator.validate({**claims, "unexpected_claim": "x"})


def test_issued_token_does_not_match_the_frozen_v1_schema() -> None:
    """Confirms the cutover actually happened: a v2-shaped token is NOT a
    valid v1 token (v1's additionalProperties:false rejects the extra
    top-level function_id/content_hash claims v2 introduces). Guards
    against silently drifting back to issuing v1 shape.
    """
    _, claims = _issue()
    schema = json.loads(GATE_TOKEN_V1_SCHEMA.read_text(encoding="utf-8"))
    with pytest.raises(Exception):
        Draft202012Validator(schema).validate(claims)


def test_content_hash_may_be_empty_for_non_content_actions() -> None:
    """app/routers/gate_check.py issues every approved token with
    content_hash=request.content_hash or "" — a gate token authorizing an
    action with no content to bind (e.g. executing a campaign step) has no
    hash to carry. v2's schema.json pattern
    (^(?:[0-9a-f]{64})?$) must keep accepting that, matching v1's existing
    permissiveness exactly (this was caught and fixed during v2 schema
    review — the empty-string case is not hypothetical, it is the actual
    live behaviour of the one real issuance call site).
    """
    _, claims = _issue(content_hash="")
    schema = json.loads(GATE_TOKEN_V2_SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(claims)
    assert claims["content_hash"] == ""


def test_content_hash_pattern_rejects_non_hex_non_empty_values() -> None:
    schema = json.loads(GATE_TOKEN_V2_SCHEMA.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    base_claims = build_claims(
        gate_decision_id=str(uuid.uuid4()),
        subject=str(uuid.uuid4()),
        content_hash=CONTENT_HASH,
        function_id=FUNCTION_ID,
    )
    for bad_hash in ("not-hex", "B" * 64, "a" * 63, "a" * 65):
        with pytest.raises(Exception):
            validator.validate({**base_claims, "content_hash": bad_hash})
