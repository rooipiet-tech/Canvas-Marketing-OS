#!/usr/bin/env python3
"""Prove the signing-key resolution order is real, not decorative.

Run standalone (from repo root):

    python services/registry/test_key_resolution.py

The alternate keypair used here is generated **ephemerally into a temporary
directory on every run and deleted afterwards**. It is deliberately never
committed: the only key material in this repository is the clearly-labelled
`services/registry/keys/dev-signing-key.*` pair.

Cases covered:

  1. With no env var set, signing and verification use the committed dev key
     and succeed, and the dev-key WARNING is emitted on stderr.
  2. With `REGISTRY_SIGNING_KEY_PATH` pointing at the ephemeral key, the
     artefact is signed with *that* key: it verifies against the ephemeral
     public key and **fails** against the dev public key. That asymmetry is
     the actual proof the env var was honoured rather than silently ignored
     in favour of the fallback.
  3. A `keyvault://` reference fails loudly instead of degrading to the dev
     key.
  4. A non-Ed25519 key (RSA) is rejected — no algorithm-confusion path.

Exits 0 with PASS, or 1 with `FAIL: <reason>`.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from common import REGISTRY_DIR, REPO_ROOT, fail
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from signing import (
    DEV_PRIVATE_KEY_PATH,
    DEV_PUBLIC_KEY_PATH,
    PRIVATE_KEY_ENV,
    PUBLIC_KEY_ENV,
    generate_keypair,
)

BUILD_SCRIPT = REGISTRY_DIR / "build_registry.py"
VERIFY_SCRIPT = REGISTRY_DIR / "verify_signature.py"


def run(script: Path, args: list[str], env_overrides: dict[str, str | None]):
    env = dict(os.environ)
    for key, value in env_overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return subprocess.run(
        [sys.executable, str(script), *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )


def expect(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def main() -> None:
    no_env = {PRIVATE_KEY_ENV: None, PUBLIC_KEY_ENV: None}

    with tempfile.TemporaryDirectory(prefix="cmos-registry-keytest-") as tmp:
        tmp_path = Path(tmp)
        alt_private = tmp_path / "alternate-test-key.priv"
        alt_public = tmp_path / "alternate-test-key.pub"
        generate_keypair(alt_private, alt_public)
        expect(
            alt_private.read_bytes() != DEV_PRIVATE_KEY_PATH.read_bytes(),
            "the ephemeral test key must differ from the committed dev key",
        )

        # --- Case 1: no env var -> dev-key fallback signs and verifies.
        dev_out = tmp_path / "dev-build"
        built = run(BUILD_SCRIPT, ["--out", str(dev_out), "--sign"], no_env)
        expect(built.returncode == 0, f"dev-key build failed: {built.stderr.strip()}")
        expect(
            "WARNING" in built.stderr and "dev-signing-key.priv" in built.stderr,
            "dev-key fallback must print an explicit WARNING to stderr on every use",
        )
        verified = run(VERIFY_SCRIPT, ["--artefact-dir", str(dev_out)], no_env)
        expect(verified.returncode == 0, f"dev-key verify failed: {verified.stderr.strip()}")

        # --- Case 2: env var -> the ephemeral key is actually used.
        alt_out = tmp_path / "alt-build"
        built_alt = run(
            BUILD_SCRIPT,
            ["--out", str(alt_out), "--sign"],
            {PRIVATE_KEY_ENV: str(alt_private), PUBLIC_KEY_ENV: str(alt_public)},
        )
        expect(built_alt.returncode == 0, f"alternate-key build failed: {built_alt.stderr.strip()}")
        expect(
            "WARNING" not in built_alt.stderr,
            "no dev-key WARNING may be printed when an explicit key path is configured",
        )
        expect(
            (alt_out / "registry.json").read_bytes() == (dev_out / "registry.json").read_bytes(),
            "the manifest itself must be byte-identical regardless of signing key",
        )
        expect(
            (alt_out / "registry.json.sig").read_text()
            != (dev_out / "registry.json.sig").read_text(),
            "signing with a different key must produce a different signature — an identical "
            "signature would mean the env var was silently ignored",
        )

        accepted = run(
            VERIFY_SCRIPT,
            ["--artefact-dir", str(alt_out)],
            {PRIVATE_KEY_ENV: str(alt_private), PUBLIC_KEY_ENV: str(alt_public)},
        )
        expect(
            accepted.returncode == 0,
            f"alternate-key artefact must verify against the alternate public key: "
            f"{accepted.stderr.strip()}",
        )

        rejected = run(VERIFY_SCRIPT, ["--artefact-dir", str(alt_out)], no_env)
        expect(
            rejected.returncode != 0,
            "an artefact signed with the alternate key must NOT verify against the dev key — "
            "if it does, the env var was ignored and the dev key was used to sign",
        )

        # --- Case 3: keyvault:// reference fails loudly.
        keyvault = run(
            BUILD_SCRIPT,
            ["--out", str(tmp_path / "kv-build"), "--sign"],
            {PRIVATE_KEY_ENV: "keyvault://kv-cmos-dev/secrets/registry-signing-key"},
        )
        expect(
            keyvault.returncode != 0,
            "a keyvault:// reference must fail loudly, never fall back to the dev key",
        )
        expect(
            "accepted-risks.md" in keyvault.stderr,
            "the keyvault:// failure must point at the accepted-risks follow-up entry",
        )

        # --- Case 4: a non-Ed25519 key is rejected.
        rsa_key_path = tmp_path / "rsa-test-key.priv"
        rsa_key_path.write_bytes(
            rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
        rsa_run = run(
            BUILD_SCRIPT,
            ["--out", str(tmp_path / "rsa-build"), "--sign"],
            {PRIVATE_KEY_ENV: str(rsa_key_path)},
        )
        expect(
            rsa_run.returncode != 0 and "Ed25519" in rsa_run.stderr,
            "a non-Ed25519 key must be rejected by name — Ed25519 is pinned",
        )

    print(f"dev key        {DEV_PRIVATE_KEY_PATH.name} / {DEV_PUBLIC_KEY_PATH.name} (committed)")
    print("alternate key  generated ephemerally in a temp dir, never committed")
    print("case 1  no env var        -> dev-key fallback signs + verifies, WARNING emitted")
    print("case 2  env var set       -> alternate key used; dev key REJECTS that artefact")
    print("case 3  keyvault:// ref   -> fails loudly, no silent fallback")
    print("case 4  RSA key supplied  -> rejected, Ed25519 pinned")
    print("PASS")


if __name__ == "__main__":
    main()
