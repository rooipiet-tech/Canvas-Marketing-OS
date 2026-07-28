"""Pluggable gate-token signing backends.

Selection is by the SIGNER_BACKEND environment variable:

    SIGNER_BACKEND=keyvault   production — Azure Key Vault remote sign
    SIGNER_BACKEND=local      tests only — in-process RSA key

Both backends satisfy the same `Signer` Protocol and produce RS256 tokens
that Publisher's verifier accepts identically (AC-16).
"""

from __future__ import annotations

import logging

from app.config import key_vault_url, local_signing_key_pem, signer_backend, signing_key_name
from app.signer.base import GATE_TOKEN_ALGORITHM, Signer
from app.signer.keyvault_signer import KeyVaultSigner
from app.signer.local_signer import LocalRSASigner

__all__ = [
    "GATE_TOKEN_ALGORITHM",
    "KeyVaultSigner",
    "LocalRSASigner",
    "Signer",
    "build_signer",
    "get_signer",
]

logger = logging.getLogger(__name__)

_CACHED_SIGNER: Signer | None = None


def build_signer(backend: str | None = None) -> Signer:
    resolved = (backend or signer_backend()).strip().lower()

    if resolved == "keyvault":
        vault_url = key_vault_url()
        if not vault_url:
            raise RuntimeError("SIGNER_BACKEND=keyvault requires KEY_VAULT_URL to be set")
        return KeyVaultSigner(vault_url, signing_key_name())

    if resolved == "local":
        logger.warning(
            "SIGNER_BACKEND=local selected: gate tokens are being signed by an "
            "in-process RSA key. This is a TEST-ONLY backend - production must "
            "run with SIGNER_BACKEND=keyvault so the private key never leaves "
            "Azure Key Vault."
        )
        return LocalRSASigner(local_signing_key_pem())

    raise RuntimeError(f"unknown SIGNER_BACKEND {resolved!r} (expected 'local' or 'keyvault')")


def get_signer(*, reload: bool = False) -> Signer:
    global _CACHED_SIGNER
    if reload or _CACHED_SIGNER is None:
        _CACHED_SIGNER = build_signer()
    return _CACHED_SIGNER
