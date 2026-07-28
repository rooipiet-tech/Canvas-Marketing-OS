"""AC-06 — gate tokens match the frozen contract and carry a canonical
JSON `resource` claim.

Pure-unit: no database needed.
"""

from __future__ import annotations

import base64
import json
import uuid
from pathlib import Path

import pytest
from app.signer.local_signer import LocalRSASigner
from app.tokens import (
    CANONICAL_JSON_SEPARATORS,
    build_resource_claim,
    issue_gate_token,
    parse_resource_claim,
)
from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[3]
GATE_TOKEN_SCHEMA = REPO_ROOT / "contracts" / "gate-token" / "schema.json"

CONTENT_HASH = "b" * 64
FUNCTION_ID = "publish.social_post"


def _decode_segment(segment: str) -> dict:
    padding = "=" * (-len(segment) % 4)
    return json.loads(base64.urlsafe_b64decode(segment + padding))


def _issue() -> tuple[str, dict]:
    signer = LocalRSASigner()
    return issue_gate_token(
        signer,
        gate_decision_id=str(uuid.uuid4()),
        subject=str(uuid.uuid4()),
        content_hash=CONTENT_HASH,
        function_id=FUNCTION_ID,
        ttl_seconds=900,
    )


def test_approved_token_matches_frozen_schema() -> None:
    token, claims = _issue()

    schema = json.loads(GATE_TOKEN_SCHEMA.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)

    # The claim set as issued validates against the frozen v1 schema...
    validator.validate(claims)
    # ...and so does what actually went out on the wire.
    header_segment, payload_segment, signature_segment = token.split(".")
    on_the_wire = _decode_segment(payload_segment)
    validator.validate(on_the_wire)
    assert on_the_wire == claims

    # additionalProperties:false is honoured — no invented top-level claim.
    assert set(claims) <= set(schema["properties"])
    assert set(schema["required"]) <= set(claims)
    assert "function_id" not in claims
    assert "content_hash" not in claims
    assert "approver" not in claims

    # RS256 only (this Key Vault SKU cannot do EdDSA).
    assert _decode_segment(header_segment)["alg"] == "RS256"
    assert signature_segment

    # Sanity: a schema-invalid extra claim really would be caught.
    with pytest.raises(Exception):
        validator.validate({**claims, "function_id": FUNCTION_ID})


def test_resource_claim_is_canonical_json() -> None:
    _, claims = _issue()
    resource = claims["resource"]

    parsed = json.loads(resource)
    # Re-serialising the parsed claim is byte-identical to the original.
    reserialised = json.dumps(parsed, sort_keys=True, separators=CANONICAL_JSON_SEPARATORS)
    assert reserialised == resource
    assert reserialised.encode("utf-8") == resource.encode("utf-8")

    # Exact expected bytes: sorted keys, zero whitespace.
    assert resource == '{"content_hash":"' + CONTENT_HASH + '","function_id":"' + FUNCTION_ID + '"}'
    assert " " not in resource

    # Argument order at the call site cannot change the bytes.
    assert build_resource_claim(content_hash=CONTENT_HASH, function_id=FUNCTION_ID) == resource
    assert build_resource_claim(function_id=FUNCTION_ID, content_hash=CONTENT_HASH) == resource

    assert parse_resource_claim(resource) == {
        "content_hash": CONTENT_HASH,
        "function_id": FUNCTION_ID,
    }

    # A semantically-equal but non-canonical serialisation is rejected,
    # so a hash comparison can never be fooled by whitespace/key order.
    non_canonical = json.dumps(parsed, sort_keys=True)  # default separators add spaces
    assert non_canonical != resource
    with pytest.raises(ValueError, match="canonical"):
        parse_resource_claim(non_canonical)

    reordered = '{"function_id":"' + FUNCTION_ID + '","content_hash":"' + CONTENT_HASH + '"}'
    with pytest.raises(ValueError, match="canonical"):
        parse_resource_claim(reordered)
