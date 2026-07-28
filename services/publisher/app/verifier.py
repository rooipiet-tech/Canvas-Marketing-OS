"""Gate-token verification (AC-08, AC-10, AC-18, AC-21).

STANDALONE BY DESIGN: this module imports only the standard library, PyJWT
and (indirectly) cryptography. It must never import from `app`, because
services/gatekeeper/tests/test_signer_parity.py loads this exact file by
path with importlib to prove both signer backends produce tokens Publisher
accepts identically — the two services share no library, and cross-service
`import app.…` would collide (both ship a top-level `app` package).

Algorithm pinning (C-4):
  * The header `alg` is inspected FIRST and must be in the pinned
    allowlist. `alg: none` and algorithm-confusion (an HS256 token
    submitted against an RSA public key used as an HMAC secret) are both
    rejected before any signature work happens, with the distinct reason
    `invalid_alg` — distinct from the four GOAL refusal reasons
    (token_absent / token_expired / content_hash_mismatch / token_replayed).
  * RS256 only. The contract also allows ES256/PS256/EdDSA, but EdDSA is
    unavailable on a standard-tier Key Vault (no Ed25519 key type at any
    SKU) and only RS256 is issued this session.
"""

from __future__ import annotations

import json
from typing import Any, Iterable

import jwt

# Refusal reasons. These strings are written verbatim into
# governance.publish_attempts.reason.
REASON_TOKEN_ABSENT = "token_absent"
REASON_TOKEN_EXPIRED = "token_expired"
REASON_TOKEN_REPLAYED = "token_replayed"
REASON_CONTENT_HASH_MISMATCH = "content_hash_mismatch"
REASON_INVALID_ALG = "invalid_alg"
REASON_TOKEN_INVALID = "token_invalid"

DEFAULT_ALLOWED_ALGORITHMS = ("RS256",)

REQUIRED_CLAIMS = ("exp", "iat", "jti", "gate_decision_id", "iss", "sub", "aud")

# Must stay byte-identical to services/gatekeeper/app/tokens.py.
CANONICAL_JSON_SEPARATORS = (",", ":")


class VerificationError(Exception):
    """Carries the exact reason string recorded in the audit row."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail or reason


def parse_resource_claim(resource: str) -> dict[str, str]:
    """Parse the canonical-JSON `resource` claim, rejecting any variance.

    The claim is re-serialised and compared byte-for-byte with the string
    that arrived, so no whitespace or key-order variation can be used to
    smuggle a different content_hash past the comparison below.
    """
    try:
        parsed = json.loads(resource)
    except (TypeError, json.JSONDecodeError) as exc:
        raise VerificationError(REASON_TOKEN_INVALID, f"resource claim is not JSON: {exc}") from exc

    if not isinstance(parsed, dict):
        raise VerificationError(REASON_TOKEN_INVALID, "resource claim must be a JSON object")
    if set(parsed) != {"content_hash", "function_id"}:
        raise VerificationError(
            REASON_TOKEN_INVALID,
            f"resource claim must hold exactly content_hash and function_id, got {sorted(parsed)}",
        )

    recanonicalised = json.dumps(parsed, sort_keys=True, separators=CANONICAL_JSON_SEPARATORS)
    if recanonicalised != resource:
        raise VerificationError(
            REASON_TOKEN_INVALID, "resource claim is not canonical JSON (byte-equality failed)"
        )
    return parsed


class GateTokenVerifier:
    """Stateless verifier. All replay state lives in Postgres, never here.

    Two independently constructed verifier objects sharing only a database
    connection MUST agree about which jti values are already consumed
    (AC-18) — which is why this class holds no seen-jti set of any kind.
    """

    def __init__(
        self,
        *,
        public_key_pem: str,
        issuer: str,
        audience: str,
        allowed_algorithms: Iterable[str] = DEFAULT_ALLOWED_ALGORITHMS,
        leeway_seconds: int = 0,
    ) -> None:
        self._public_key_pem = public_key_pem
        self._issuer = issuer
        self._audience = audience
        self._allowed_algorithms = tuple(allowed_algorithms)
        self._leeway_seconds = leeway_seconds
        if not self._allowed_algorithms:
            raise ValueError("at least one algorithm must be pinned")

    @property
    def allowed_algorithms(self) -> tuple[str, ...]:
        return self._allowed_algorithms

    def _pin_algorithm(self, token: str) -> str:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise VerificationError(REASON_TOKEN_INVALID, f"malformed token header: {exc}") from exc

        alg = header.get("alg")
        if alg is None:
            raise VerificationError(REASON_INVALID_ALG, "token header carries no alg")
        if alg not in self._allowed_algorithms:
            # Covers alg:none and algorithm-confusion (e.g. HS256 signed
            # with the RSA public PEM used as an HMAC secret).
            raise VerificationError(
                REASON_INVALID_ALG,
                f"alg {alg!r} is not pinned (allowed: {', '.join(self._allowed_algorithms)})",
            )
        return alg

    def verify(self, token: str | None) -> dict[str, Any]:
        """Verify signature, alg, expiry and claim shape. No replay check."""
        if not token or not token.strip():
            raise VerificationError(REASON_TOKEN_ABSENT, "no gate token supplied")

        self._pin_algorithm(token)

        try:
            claims = jwt.decode(
                token,
                key=self._public_key_pem,
                algorithms=list(self._allowed_algorithms),
                issuer=self._issuer,
                audience=self._audience,
                leeway=self._leeway_seconds,
                options={"require": list(REQUIRED_CLAIMS), "verify_signature": True},
            )
        except jwt.ExpiredSignatureError as exc:
            raise VerificationError(REASON_TOKEN_EXPIRED, str(exc)) from exc
        except jwt.InvalidAlgorithmError as exc:
            raise VerificationError(REASON_INVALID_ALG, str(exc)) from exc
        except jwt.PyJWTError as exc:
            raise VerificationError(REASON_TOKEN_INVALID, str(exc)) from exc

        if "resource" not in claims:
            raise VerificationError(
                REASON_TOKEN_INVALID,
                "gate token carries no resource claim, so it binds no content_hash",
            )
        return claims

    def bound_content_hash(self, claims: dict[str, Any]) -> str:
        return parse_resource_claim(claims["resource"])["content_hash"]

    def bound_function_id(self, claims: dict[str, Any]) -> str:
        return parse_resource_claim(claims["resource"])["function_id"]
