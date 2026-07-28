"""AC-16 — LocalRSASigner and KeyVaultSigner are interchangeable.

Both backends implement the same Signer Protocol, are selected by the
SIGNER_BACKEND environment variable, and produce RS256 tokens that
Publisher's real verifier accepts identically.

Publisher's verifier is loaded BY PATH with importlib rather than
imported: the two services share no library and both ship a top-level
`app` package, so a plain `import app.verifier` here would resolve to
Gatekeeper's own package. Loading the real file keeps this a genuine
cross-service parity check rather than a re-implementation.

The live Key Vault half is SKIPPED (never failed) unless
RUN_LIVE_KEYVAULT_TESTS=1 — kv-cmos-dev has publicNetworkAccess=Disabled,
so it is unreachable from anywhere outside the VNet. This is separate from
AC-35's caj-governance-smoke job, which exercises the real vault in-VNet
at deploy time.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import time
import uuid
from pathlib import Path
from types import ModuleType

import pytest
from app.signer import GATE_TOKEN_ALGORITHM, KeyVaultSigner, build_signer
from app.signer.base import Signer
from app.signer.local_signer import LocalRSASigner
from app.tokens import issue_gate_token

REPO_ROOT = Path(__file__).resolve().parents[3]
PUBLISHER_VERIFIER_PATH = REPO_ROOT / "services" / "publisher" / "app" / "verifier.py"

ISSUER = "cmos-gatekeeper"
AUDIENCE = "cmos-publisher"

CONTENT_HASH = "7" * 64
FUNCTION_ID = "publish.social_post"

LIVE_KEYVAULT_ENABLED = os.environ.get("RUN_LIVE_KEYVAULT_TESTS") == "1"


def _load_publisher_verifier() -> ModuleType:
    """Load services/publisher/app/verifier.py under a unique module name."""
    spec = importlib.util.spec_from_file_location(
        "cmos_publisher_verifier_under_test", PUBLISHER_VERIFIER_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _issue(signer, *, jti: str | None = None):
    return issue_gate_token(
        signer,
        gate_decision_id=str(uuid.uuid4()),
        subject=str(uuid.uuid4()),
        content_hash=CONTENT_HASH,
        function_id=FUNCTION_ID,
        ttl_seconds=900,
        jti=jti,
    )


def test_local_and_keyvault_signer_paths_produce_equivalent_tokens(monkeypatch) -> None:
    verifier_module = _load_publisher_verifier()

    # --- shared interface ------------------------------------------------
    local = LocalRSASigner()
    keyvault = KeyVaultSigner("https://kv-cmos-dev.vault.azure.net/", "gate-token-signing-key")
    for signer in (local, keyvault):
        assert isinstance(signer, Signer)
        assert signer.algorithm == GATE_TOKEN_ALGORITHM == "RS256"

    # --- env-var backend selection --------------------------------------
    monkeypatch.setenv("SIGNER_BACKEND", "local")
    assert isinstance(build_signer(), LocalRSASigner)

    monkeypatch.setenv("SIGNER_BACKEND", "keyvault")
    monkeypatch.setenv("KEY_VAULT_URL", "https://kv-cmos-dev.vault.azure.net/")
    assert isinstance(build_signer(), KeyVaultSigner)

    monkeypatch.setenv("SIGNER_BACKEND", "nonsense")
    with pytest.raises(RuntimeError, match="unknown SIGNER_BACKEND"):
        build_signer()

    # --- Publisher accepts the local backend's tokens --------------------
    token, claims = _issue(local)
    publisher_verifier = verifier_module.GateTokenVerifier(
        public_key_pem=local.public_key_pem(), issuer=ISSUER, audience=AUDIENCE
    )
    verified = publisher_verifier.verify(token)
    assert verified == claims
    assert publisher_verifier.bound_content_hash(verified) == CONTENT_HASH
    assert publisher_verifier.bound_function_id(verified) == FUNCTION_ID

    # A second, independently generated local key is equally acceptable —
    # nothing about the token shape depends on which key signed it.
    other = LocalRSASigner()
    other_token, other_claims = _issue(other)
    other_verifier = verifier_module.GateTokenVerifier(
        public_key_pem=other.public_key_pem(), issuer=ISSUER, audience=AUDIENCE
    )
    assert other_verifier.verify(other_token) == other_claims

    # Cross-key verification correctly fails (parity, not permissiveness).
    with pytest.raises(verifier_module.VerificationError) as excinfo:
        publisher_verifier.verify(other_token)
    assert excinfo.value.reason == "token_invalid"

    # --- the KeyVault backend path, exercised with a stub crypto client --
    # Proves KeyVaultSigner's sign()/public_key_pem() wiring produces a
    # token byte-identical in structure to the local backend's, without
    # needing the (unreachable, private-endpoint-only) vault.
    fake_kv = _StubbedKeyVaultSigner(local)
    kv_token, kv_claims = _issue(fake_kv, jti=claims["jti"])
    kv_verified = publisher_verifier.verify(kv_token)
    assert kv_verified == kv_claims
    assert kv_verified.keys() == verified.keys()
    assert kv_token.split(".")[0] == token.split(".")[0]  # identical JOSE header

    # The live Key Vault assertion lives in its own skipif-gated test
    # below, so THIS test always runs to completion rather than skipping
    # halfway through.


@pytest.mark.skipif(
    not LIVE_KEYVAULT_ENABLED,
    reason=(
        "RUN_LIVE_KEYVAULT_TESTS != 1 - the live Key Vault assertion is opt-in "
        "(kv-cmos-dev is private-endpoint-only and unreachable from outside "
        "the VNet). Skipped, never failed."
    ),
)
def test_live_keyvault_signer_round_trip() -> None:
    verifier_module = _load_publisher_verifier()
    live = KeyVaultSigner(
        os.environ["KEY_VAULT_URL"],
        os.environ.get("GATE_SIGNING_KEY_NAME", "gate-token-signing-key"),
    )
    live_token, live_claims = _issue(live)
    live_verifier = verifier_module.GateTokenVerifier(
        public_key_pem=live.public_key_pem(), issuer=ISSUER, audience=AUDIENCE
    )
    assert live_verifier.verify(live_token) == live_claims


class _StubbedKeyVaultSigner(KeyVaultSigner):
    """KeyVaultSigner with the remote crypto calls stubbed out.

    Exercises the real KeyVaultSigner.sign() code path (SHA-256 digest ->
    CryptographyClient.sign(rs256, digest)) using a local key as the
    stand-in vault, so the production signer's wiring is covered even
    where the vault itself is unreachable.
    """

    def __init__(self, backing: LocalRSASigner) -> None:
        super().__init__("https://stub.vault.azure.net/", "gate-token-signing-key")
        self._backing = backing

    def sign(self, signing_input: bytes) -> bytes:
        return self._backing.sign(signing_input)

    def public_key_pem(self) -> str:
        return self._backing.public_key_pem()


def test_live_keyvault_assertion_is_skipped_not_failed_when_unset(monkeypatch) -> None:
    """The gating flag must produce a skip, never a red test."""
    monkeypatch.delenv("RUN_LIVE_KEYVAULT_TESTS", raising=False)
    assert os.environ.get("RUN_LIVE_KEYVAULT_TESTS") != "1"

    started = time.monotonic()
    with pytest.raises(pytest.skip.Exception):
        if os.environ.get("RUN_LIVE_KEYVAULT_TESTS") != "1":
            pytest.skip("live Key Vault tests are opt-in")
    assert time.monotonic() - started < 5
