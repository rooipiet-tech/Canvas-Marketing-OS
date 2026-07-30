"""TEST-ONLY RSA signer.

This is the ONLY module in the service permitted to hold or generate RSA
private key material. Production runs with SIGNER_BACKEND=keyvault, where
the private key is created by Bicep inside Key Vault and never leaves it
(see app/signer/keyvault_signer.py and infra/modules/governance/
signing-key.bicep).

Selecting this backend in a deployed environment is a misconfiguration:
app/signer/__init__.py logs a loud warning when it happens.
"""

from __future__ import annotations

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app.signer.base import GATE_TOKEN_ALGORITHM

DEFAULT_KEY_SIZE = 2048


class LocalRSASigner:
    """RS256 signer backed by an in-process RSA private key. Tests only."""

    def __init__(self, private_key_pem: str | bytes | None = None) -> None:
        if private_key_pem is None:
            # Test-only key material. Never reached under
            # SIGNER_BACKEND=keyvault.
            self._private_key = rsa.generate_private_key(
                public_exponent=65537, key_size=DEFAULT_KEY_SIZE
            )
        else:
            if isinstance(private_key_pem, str):
                private_key_pem = private_key_pem.encode("utf-8")
            loaded = serialization.load_pem_private_key(private_key_pem, password=None)
            if not isinstance(loaded, rsa.RSAPrivateKey):
                raise TypeError("LocalRSASigner requires an RSA private key")
            self._private_key = loaded

    @property
    def algorithm(self) -> str:
        return GATE_TOKEN_ALGORITHM

    def sign(self, signing_input: bytes) -> bytes:
        return self._private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())

    def public_key_pem(self) -> str:
        return (
            self._private_key.public_key()
            .public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            .decode("utf-8")
        )

    def private_key_pem(self) -> str:
        """Export the test key so a test can hand it to a verifier fixture."""
        return self._private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("utf-8")
