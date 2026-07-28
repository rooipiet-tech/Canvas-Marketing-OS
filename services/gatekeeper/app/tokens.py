"""Gate-token construction (AC-06).

contracts/gate-token/schema.json is FROZEN v1 with
`"additionalProperties": false`, and its only optional free-form field is
`resource` (a string). function_id and content_hash therefore cannot
become top-level JWT claims: they are packed into `resource` as CANONICAL
JSON —

    json.dumps({"content_hash": ..., "function_id": ...},
               sort_keys=True, separators=(",", ":"))

— i.e. keys sorted, zero whitespace, deterministic byte-for-byte. Publisher
re-serialises the parsed claim and requires byte-equality before trusting
the content_hash, so no whitespace/ordering variance can slip a different
string past a hash comparison.

The approver is NOT a token claim: it is resolved server-side through
gate_decision_id -> gate_decisions.decided_by (which per AC-32 holds the
Easy-Auth-authenticated principal). Timestamps are iat/exp. No new
top-level claim is introduced anywhere.
"""

from __future__ import annotations

import base64
import json
import time
import uuid
from typing import Any

from app.config import token_audience, token_issuer, token_ttl_seconds

# Canonical JSON serialisation parameters for the `resource` claim.
# Any change here is a wire-format change and must be mirrored in
# services/publisher/app/verifier.py.
CANONICAL_JSON_SEPARATORS = (",", ":")


def build_resource_claim(*, content_hash: str, function_id: str) -> str:
    """Canonical-JSON `resource` claim: sorted keys, no whitespace."""
    return json.dumps(
        {"content_hash": content_hash, "function_id": function_id},
        sort_keys=True,
        separators=CANONICAL_JSON_SEPARATORS,
    )


def parse_resource_claim(resource: str) -> dict[str, str]:
    """Parse a `resource` claim, rejecting any non-canonical serialisation."""
    parsed = json.loads(resource)
    if not isinstance(parsed, dict):
        raise ValueError("resource claim must be a JSON object")
    if set(parsed) != {"content_hash", "function_id"}:
        raise ValueError(
            "resource claim must contain exactly content_hash and function_id, "
            f"got {sorted(parsed)}"
        )
    recanonicalised = json.dumps(parsed, sort_keys=True, separators=CANONICAL_JSON_SEPARATORS)
    if recanonicalised != resource:
        raise ValueError("resource claim is not canonical JSON (byte-equality check failed)")
    return parsed


def _b64url(raw: bytes) -> str:
    """base64url WITHOUT padding — the JWS encoding (RFC 7515 §2).

    Not `base64.b64encode`: the standard alphabet's '+' and '/' are unsafe
    in the compact serialisation and in any URI carrying it (L-0004).
    """
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def build_claims(
    *,
    gate_decision_id: str,
    subject: str,
    content_hash: str,
    function_id: str,
    issuer: str | None = None,
    audience: str | None = None,
    ttl_seconds: int | None = None,
    issued_at: int | None = None,
    jti: str | None = None,
) -> dict[str, Any]:
    """Build a claim set valid against the frozen gate-token schema."""
    now = int(issued_at if issued_at is not None else time.time())
    ttl = int(ttl_seconds if ttl_seconds is not None else token_ttl_seconds())
    return {
        "iss": issuer or token_issuer(),
        "sub": subject,
        "aud": audience or token_audience(),
        "iat": now,
        "exp": now + ttl,
        "jti": jti or str(uuid.uuid4()),
        "gate_decision_id": str(gate_decision_id),
        "resource": build_resource_claim(content_hash=content_hash, function_id=function_id),
    }


def sign_claims(signer: Any, claims: dict[str, Any]) -> str:
    """Assemble a compact JWS using the pluggable signer's raw signature."""
    header = {"alg": signer.algorithm, "typ": "JWT"}
    header_segment = _b64url(json.dumps(header, separators=CANONICAL_JSON_SEPARATORS).encode())
    payload_segment = _b64url(json.dumps(claims, separators=CANONICAL_JSON_SEPARATORS).encode())
    signing_input = f"{header_segment}.{payload_segment}".encode("ascii")
    signature = signer.sign(signing_input)
    return f"{header_segment}.{payload_segment}.{_b64url(signature)}"


def issue_gate_token(
    signer: Any,
    *,
    gate_decision_id: str,
    subject: str,
    content_hash: str,
    function_id: str,
    ttl_seconds: int | None = None,
    issued_at: int | None = None,
    jti: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Return (compact JWT, claim set)."""
    claims = build_claims(
        gate_decision_id=gate_decision_id,
        subject=subject,
        content_hash=content_hash,
        function_id=function_id,
        ttl_seconds=ttl_seconds,
        issued_at=issued_at,
        jti=jti,
    )
    return sign_claims(signer, claims), claims
