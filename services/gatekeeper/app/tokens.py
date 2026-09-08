"""Gate-token construction (AC-06).

TD-14 v2 CONTRACT WINDOW: Gatekeeper issues ONLY v2 tokens now
(contracts/gate-token/v2/schema.json) — function_id and content_hash are
first-class, required top-level JWT claims. No `resource` claim is built
or included at all; v1's canonical-JSON packing
(governance_lib.resource_claim.canonicalize_resource_claim) is no longer
called from this module. Gatekeeper is the sole gate-token issuer in this
system, so this is a clean, atomic cutover on the build side — unlike
Publisher (the sole verifier), which must still ACCEPT a v1 token that
could be in flight across an independent deploy of the two services (see
services/publisher/app/verifier.py and
governance_lib.resource_claim.extract_function_id_and_content_hash).

content_hash may be the empty string — a gate token authorizing an action
with no content to bind (e.g. "execute this campaign step") carries
content_hash="" (see app/routers/gate_check.py's
`content_hash=request.content_hash or ""`), which v2/schema.json's pattern
`^(?:[0-9a-f]{64})?$` explicitly allows, matching v1's existing
permissiveness.

The approver is NOT a token claim: it is resolved server-side through
gate_decision_id -> gate_decisions.decided_by (which per AC-32 holds the
Easy-Auth-authenticated principal). Timestamps are iat/exp.

TD-08: CANONICAL_JSON_SEPARATORS (still needed for the JWS header/payload
serialisation below, independent of the resource-claim question) used to
be hand-duplicated here and in services/publisher/app/verifier.py. Both
import services/governance-lib/governance_lib/resource_claim.py's single
implementation instead.
"""

from __future__ import annotations

import base64
import json
import time
import uuid
from typing import Any

from governance_lib.resource_claim import CANONICAL_JSON_SEPARATORS

from app.config import token_audience, token_issuer, token_ttl_seconds

__all__ = [
    "CANONICAL_JSON_SEPARATORS",
    "build_claims",
    "issue_gate_token",
    "sign_claims",
]


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
    """Build a claim set valid against the v2 gate-token schema
    (contracts/gate-token/v2/schema.json) — function_id and content_hash
    are first-class top-level claims, not packed into a resource string.
    """
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
        "function_id": function_id,
        "content_hash": content_hash,
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
