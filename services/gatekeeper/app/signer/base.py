"""The Signer interface both gate-token signing backends implement.

Only RS256 is implemented as the default (and only) production algorithm.
The gate-token contract also permits ES256/PS256/EdDSA, but:

  * EdDSA is impossible here — kv-cmos-dev is a standard-tier
    (software-protected) Key Vault, and Azure Key Vault supports RSA and
    EC (P-256/384/521/256K) only; there is no Ed25519 key type at any SKU.
  * ES256/PS256 remain contract-valid but are not wired this session.

Publisher pins RS256 accordingly (services/publisher/app/verifier.py).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

# The one algorithm this session issues and verifies. Documented above.
GATE_TOKEN_ALGORITHM = "RS256"


@runtime_checkable
class Signer(Protocol):
    """Produces a raw JWS signature over a JWT signing input.

    Implementations MUST NOT expose private key material: `public_key_pem`
    is the only key export, and it is public by definition.
    """

    @property
    def algorithm(self) -> str:
        """JWS `alg` header value this signer produces (always RS256 here)."""
        ...

    def sign(self, signing_input: bytes) -> bytes:
        """Return the raw signature bytes over `signing_input`."""
        ...

    def public_key_pem(self) -> str:
        """Return the SubjectPublicKeyInfo PEM of the verification key."""
        ...
