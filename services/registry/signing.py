"""Ed25519 signing/verification for the registry artefact.

Key resolution order (AC-04) — deliberately config-first so that moving to a
production signing key is a configuration swap, never a code change:

  1. ``REGISTRY_SIGNING_KEY_PATH`` / ``REGISTRY_SIGNING_PUBLIC_KEY_PATH``
     environment variables. A ``keyvault://`` reference is recognised and
     fails loudly: Key Vault is not reachable from this scope (public
     network access is Disabled, no in-VNet runner), and silently falling
     back to the dev key when an operator explicitly asked for the
     production key would be the worst possible outcome.
  2. The committed development keypair under ``services/registry/keys/``.
     Every use of this fallback prints an explicit WARNING to stderr — a
     static README note is not enough, the operator must see it in the run
     output every single time.

Algorithm is pinned to Ed25519. There is no code path that accepts
``alg: none`` or any HMAC/shared-secret construction, matching the
asymmetric-only convention already frozen in contracts/gate-token/spec.md.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

from common import REGISTRY_DIR, fail, rel_or_str, warn
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

SIGNATURE_ALGORITHM = "Ed25519"

KEYS_DIR = REGISTRY_DIR / "keys"
DEV_PRIVATE_KEY_PATH = KEYS_DIR / "dev-signing-key.priv"
DEV_PUBLIC_KEY_PATH = KEYS_DIR / "dev-signing-key.pub"

PRIVATE_KEY_ENV = "REGISTRY_SIGNING_KEY_PATH"
PUBLIC_KEY_ENV = "REGISTRY_SIGNING_PUBLIC_KEY_PATH"

KEYVAULT_SCHEME = "keyvault://"

_KEYVAULT_MESSAGE = (
    "{env}={value!r} is a Key Vault reference, but no Key Vault-held signing key "
    "exists in this scope and the vault is not reachable (publicNetworkAccess "
    "Disabled, no in-VNet runner). Refusing to silently fall back to the "
    "non-production dev key. See the 'Registry artefact is signed with a "
    "committed development key' entry in docs/accepted-risks.md, which records "
    "the Key Vault-held production signing key as a named follow-up."
)

_FALLBACK_MESSAGE = (
    "{env} is not set - falling back to the COMMITTED DEVELOPMENT signing key at "
    "{path}. This key is public, confers NO security, and must never be used for "
    "a production artefact. Set {env} to a real key path to override."
)


def resolve_private_key_path() -> Path:
    """Resolve the signing private key, env var first, dev key as loud fallback."""
    return _resolve_key_path(PRIVATE_KEY_ENV, DEV_PRIVATE_KEY_PATH)


def resolve_public_key_path() -> Path:
    """Resolve the verification public key, env var first, dev key as loud fallback."""
    return _resolve_key_path(PUBLIC_KEY_ENV, DEV_PUBLIC_KEY_PATH)


def _resolve_key_path(env_var: str, fallback: Path) -> Path:
    configured = os.environ.get(env_var, "").strip()
    if configured:
        if configured.lower().startswith(KEYVAULT_SCHEME):
            fail(_KEYVAULT_MESSAGE.format(env=env_var, value=configured))
        candidate = Path(configured).expanduser()
        if not candidate.is_file():
            fail(f"{env_var}={configured!r} does not point at a readable key file")
        return candidate

    if not fallback.is_file():
        fail(
            f"{env_var} is unset and the fallback development key {rel_or_str(fallback)} "
            "is missing — cannot sign or verify"
        )
    warn(_FALLBACK_MESSAGE.format(env=env_var, path=rel_or_str(fallback)))
    return fallback


def load_private_key(path: Path) -> Ed25519PrivateKey:
    """Load a PEM private key, rejecting anything that is not Ed25519."""
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        fail(
            f"{rel_or_str(path)} is not an {SIGNATURE_ALGORITHM} private key "
            f"(got {type(key).__name__}); only {SIGNATURE_ALGORITHM} is accepted"
        )
    return key


def load_public_key(path: Path) -> Ed25519PublicKey:
    """Load a PEM public key, rejecting anything that is not Ed25519."""
    key = serialization.load_pem_public_key(path.read_bytes())
    if not isinstance(key, Ed25519PublicKey):
        fail(
            f"{rel_or_str(path)} is not an {SIGNATURE_ALGORITHM} public key "
            f"(got {type(key).__name__}); only {SIGNATURE_ALGORITHM} is accepted"
        )
    return key


def sign_bytes(payload: bytes, private_key_path: Path | None = None) -> str:
    """Sign `payload` with Ed25519, returning a base64 detached signature.

    Ed25519 is deterministic (RFC 8032), so signing identical bytes with
    the same key yields an identical signature — the detached signature
    file participates in byte-identical reproducibility rather than
    breaking it.
    """
    path = private_key_path or resolve_private_key_path()
    signature = load_private_key(path).sign(payload)
    return base64.b64encode(signature).decode("ascii")


def verify_bytes(payload: bytes, signature_b64: str, public_key_path: Path | None = None) -> bool:
    """Verify a base64 Ed25519 detached signature over `payload`."""
    path = public_key_path or resolve_public_key_path()
    try:
        signature = base64.b64decode(signature_b64.strip(), validate=True)
    except (ValueError, base64.binascii.Error):
        return False
    try:
        load_public_key(path).verify(signature, payload)
    except InvalidSignature:
        return False
    return True


def public_key_fingerprint(path: Path) -> str:
    """Short, stable fingerprint of a public key, for run output."""
    raw = load_public_key(path).public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.b16encode(raw).decode("ascii").lower()[:16]


def generate_keypair(private_path: Path, public_path: Path) -> None:
    """Write a fresh Ed25519 keypair as PEM files.

    Used to mint the committed development key and, in tests, to mint a
    throwaway alternate keypair in a temporary directory. Never used to
    create production key material.
    """
    private_path.parent.mkdir(parents=True, exist_ok=True)
    public_path.parent.mkdir(parents=True, exist_ok=True)
    private_key = Ed25519PrivateKey.generate()
    private_path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    public_path.write_bytes(
        private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
