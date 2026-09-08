"""Gate-token verification (AC-08, AC-10, AC-18, AC-21).

STANDALONE BY DESIGN: this module must never import from `app`, because
services/gatekeeper/tests/test_signer_parity.py loads this exact file by
path with importlib to prove both signer backends produce tokens Publisher
accepts identically — both services ship a top-level `app` package, and
cross-service `import app.…` would collide, resolving to whichever
service's `app` package happened to be on sys.path first. This module DOES
import governance_lib (TD-08) — a neutral shared package with no `app.*`
in it, which satisfies the same constraint the "never import from `app`"
rule exists for: nothing here can accidentally resolve to Gatekeeper's own
`app` package.

TD-08: CANONICAL_JSON_SEPARATORS and parse_resource_claim used to be
hand-duplicated here and in services/gatekeeper/app/tokens.py, each
carrying a comment that the two "must stay byte-identical". Both now
import services/governance-lib/governance_lib/resource_claim.py's single
implementation; this module's own parse_resource_claim wraps it only to
translate a bare ValueError into this service's VerificationError.

TD-14 v2 CONTRACT WINDOW: Gatekeeper now issues ONLY v2 tokens
(contracts/gate-token/v2/schema.json — function_id/content_hash as
first-class top-level claims), but this verifier still ACCEPTS EITHER v1
or v2 tokens. Gatekeeper and Publisher are deployed as independent
Container Apps with independent CI pipelines (docs/architecture/
09-technical-debt.md TD-14's own "Sequencing" note flags exactly this
risk) — a real rollout could have a window where Publisher's new verifier
is live before Gatekeeper switches to v2 issuance, or the reverse, and
either ordering must work. bound_content_hash()/bound_function_id() below
delegate to governance_lib.resource_claim.extract_function_id_and_content_hash,
which shape-detects between the two forms; verify() no longer hard-requires
a `resource` claim, since a v2 token legitimately has none.

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

from typing import Any, Iterable

import jwt
from governance_lib.resource_claim import (
    extract_function_id_and_content_hash as _extract_function_id_and_content_hash,
)
from governance_lib.resource_claim import parse_resource_claim as _parse_resource_claim

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


class VerificationError(Exception):
    """Carries the exact reason string recorded in the audit row."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail or reason


def parse_resource_claim(resource: str) -> dict[str, str]:
    """Parse the canonical-JSON `resource` claim, rejecting any variance.

    Delegates to governance_lib.resource_claim.parse_resource_claim (TD-08)
    and translates its bare ValueError/JSONDecodeError into this service's
    own VerificationError(REASON_TOKEN_INVALID, ...), which is the shape
    every other refusal in this module already carries.
    """
    try:
        return _parse_resource_claim(resource)
    except ValueError as exc:
        raise VerificationError(REASON_TOKEN_INVALID, str(exc)) from exc


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

        # Validates the claim set carries a usable content_hash/function_id
        # binding, in either contract version — see _bound_taxonomy_claims.
        # The result is discarded here; bound_content_hash/bound_function_id
        # re-derive it from the same claims dict when the caller actually
        # needs the values, rather than threading them back out of verify().
        self._bound_taxonomy_claims(claims)
        return claims

    def _bound_taxonomy_claims(self, claims: dict[str, Any]) -> dict[str, str]:
        """Shape-detects v1 (resource-packed) vs v2 (top-level) claims —
        see this module's docstring and governance_lib.resource_claim's own
        extract_function_id_and_content_hash for why both must be accepted.
        """
        try:
            return _extract_function_id_and_content_hash(claims)
        except ValueError as exc:
            raise VerificationError(REASON_TOKEN_INVALID, str(exc)) from exc

    def bound_content_hash(self, claims: dict[str, Any]) -> str:
        return self._bound_taxonomy_claims(claims)["content_hash"]

    def bound_function_id(self, claims: dict[str, Any]) -> str:
        return self._bound_taxonomy_claims(claims)["function_id"]
