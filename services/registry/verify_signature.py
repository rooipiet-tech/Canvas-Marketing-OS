#!/usr/bin/env python3
"""Verify the registry artefact's detached Ed25519 signature.

Run standalone (from repo root):

    python services/registry/verify_signature.py --artefact-dir services/registry/dist
    python services/registry/verify_signature.py --registry out/registry.json \\
        --signature out/registry.json.sig

Exits 0 and prints PASS when the signature verifies against the resolved
public key. Exits 1 with `FAIL: <reason>` if the content has been tampered
with, the signature is malformed, or the key cannot be resolved.

Ed25519 only. There is no `alg` field to downgrade, no `alg: none` branch and
no HMAC/shared-secret path anywhere in this file or in `signing.py`.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from common import fail, rel_or_str
from signing import (
    SIGNATURE_ALGORITHM,
    public_key_fingerprint,
    resolve_public_key_path,
    verify_bytes,
)

DEFAULT_ARTEFACT_DIR = Path("services/registry/dist")
REGISTRY_FILENAME = "registry.json"
SIGNATURE_FILENAME = "registry.json.sig"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify the registry artefact's detached Ed25519 signature."
    )
    parser.add_argument(
        "--artefact-dir",
        type=Path,
        default=DEFAULT_ARTEFACT_DIR,
        help=f"directory holding {REGISTRY_FILENAME} and {SIGNATURE_FILENAME}",
    )
    parser.add_argument("--registry", type=Path, help=f"explicit path to {REGISTRY_FILENAME}")
    parser.add_argument("--signature", type=Path, help=f"explicit path to {SIGNATURE_FILENAME}")
    parser.add_argument(
        "--public-key",
        type=Path,
        help="explicit public key path, bypassing env-var/dev-key resolution",
    )
    args = parser.parse_args()

    registry_path = args.registry or (args.artefact_dir / REGISTRY_FILENAME)
    signature_path = args.signature or (args.artefact_dir / SIGNATURE_FILENAME)

    if not registry_path.is_file():
        fail(f"registry manifest not found at {rel_or_str(registry_path)}")
    if not signature_path.is_file():
        fail(
            f"detached signature not found at {rel_or_str(signature_path)} — "
            "build with --sign first"
        )

    public_key_path = args.public_key or resolve_public_key_path()
    if not public_key_path.is_file():
        fail(f"public key not found at {rel_or_str(public_key_path)}")

    payload = registry_path.read_bytes()
    signature = signature_path.read_text(encoding="utf-8")

    if not verify_bytes(payload, signature, public_key_path):
        fail(
            f"{SIGNATURE_ALGORITHM} signature verification FAILED for "
            f"{rel_or_str(registry_path)} against key {rel_or_str(public_key_path)} — "
            "the manifest content and the signature do not match (content tampered, "
            "signature tampered, or a different signing key was used)"
        )

    print(f"algorithm      {SIGNATURE_ALGORITHM}")
    print(f"manifest       {rel_or_str(registry_path)} ({len(payload)} bytes)")
    print(f"public key     {rel_or_str(public_key_path)}")
    print(f"key fingerprint {public_key_fingerprint(public_key_path)}")
    print("PASS")


if __name__ == "__main__":
    main()
