"""Production RS256 signer — Azure Key Vault remote sign.

The private key is created by infra/modules/governance/signing-key.bicep as
a Microsoft.KeyVault/vaults/keys resource (kty RSA, 2048-bit). It is never
generated outside the vault, never imported, never exported: this module
only ever calls CryptographyClient.sign(), which performs the RSA
operation inside Key Vault and returns the signature bytes. There is no
code path in this file that can materialise private key material — the
`cryptography` primitives used below are public-key-only (JWK -> SPKI PEM).

Reachability: kv-cmos-dev has publicNetworkAccess=Disabled, so this only
works from inside the VNet (the Container Apps environment is VNet-
integrated and the privatelink.vaultcore.azure.net zone is linked). RBAC
grants nothing by default (L-0011), hence the explicit sign-capable role
assignment for the Gatekeeper identity in signing-key.bicep.

Azure SDK imports are deliberately lazy (inside methods) so this module
imports cleanly in a test environment where azure-keyvault-keys is not
installed.
"""

from __future__ import annotations

import hashlib

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.signer.base import GATE_TOKEN_ALGORITHM


def _int_from_bytes(raw: bytes) -> int:
    return int.from_bytes(raw, byteorder="big")


class KeyVaultSigner:
    """RS256 signer whose private key never leaves Azure Key Vault."""

    def __init__(
        self,
        vault_url: str,
        key_name: str,
        *,
        credential: object | None = None,
        crypto_client: object | None = None,
        key_client: object | None = None,
    ) -> None:
        if not vault_url:
            raise ValueError("KeyVaultSigner requires a vault_url")
        self._vault_url = vault_url
        self._key_name = key_name
        self._credential = credential
        self._crypto_client = crypto_client
        self._key_client = key_client
        self._public_key_pem: str | None = None

    @property
    def algorithm(self) -> str:
        return GATE_TOKEN_ALGORITHM

    def _get_credential(self) -> object:
        if self._credential is None:
            from azure.identity import DefaultAzureCredential

            self._credential = DefaultAzureCredential()
        return self._credential

    def _get_key_client(self) -> object:
        if self._key_client is None:
            from azure.keyvault.keys import KeyClient

            self._key_client = KeyClient(
                vault_url=self._vault_url, credential=self._get_credential()
            )
        return self._key_client

    def _get_crypto_client(self) -> object:
        if self._crypto_client is None:
            from azure.keyvault.keys.crypto import CryptographyClient

            key = self._get_key_client().get_key(self._key_name)
            self._crypto_client = CryptographyClient(key, credential=self._get_credential())
        return self._crypto_client

    def sign(self, signing_input: bytes) -> bytes:
        """Remote-sign: SHA-256 the input locally, sign the digest in-vault."""
        from azure.keyvault.keys.crypto import SignatureAlgorithm

        digest = hashlib.sha256(signing_input).digest()
        result = self._get_crypto_client().sign(SignatureAlgorithm.rs256, digest)
        return result.signature

    def public_key_pem(self) -> str:
        """Fetch the PUBLIC half of the vault key and render it as SPKI PEM."""
        if self._public_key_pem is not None:
            return self._public_key_pem

        key = self._get_key_client().get_key(self._key_name)
        jwk = key.key
        public_numbers = rsa.RSAPublicNumbers(
            e=_int_from_bytes(jwk.e), n=_int_from_bytes(jwk.n)
        )
        public_key = public_numbers.public_key()
        self._public_key_pem = public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("utf-8")
        return self._public_key_pem
