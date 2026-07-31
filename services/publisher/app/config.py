"""Publisher runtime configuration, read from the process environment."""

from __future__ import annotations

import os

# The ONE algorithm Publisher accepts. See app/verifier.py for why RS256
# and not EdDSA (this Key Vault SKU has no Ed25519 key type at all).
DEFAULT_ALLOWED_ALGORITHMS = ("RS256",)

# ---------------------------------------------------------------------
# Buffer / dry-run / proof-circuit constants (plan step 14; AC-07/08/09/30;
# DE-3; GOAL "behind a dry-run flag" / "free-tier cap 10 scheduled posts").
# ---------------------------------------------------------------------


def publisher_dry_run() -> bool:
    """Default dry-run (true). Set PUBLISHER_DRY_RUN=false to flip to live
    mode -- but see vault_lookup.py: a request whose asset_id resolves to
    a loop-proof-circuit-tagged asset ALWAYS stays dry-run regardless of
    this flag's value (AC-08(e), AC-30)."""
    return os.environ.get("PUBLISHER_DRY_RUN", "true").strip().lower() not in (
        "false",
        "0",
        "no",
    )


# Buffer's free-tier plan caps queued posts at 10 (DE-3: an ASSUMPTION
# sourced from the GOAL text, not independently verifiable from any file
# in this repo -- see .loop/domain.md DE-3). Enforced as a live list_queue
# count check (app/buffer_client.py), not a static config value alone.
BUFFER_FREE_TIER_QUEUE_CAP = 10

# Buffer channel/org id map, mapping ALL 3 known channel ids + org, so
# the GOAL-prose transposition error (.loop/spec.json's v3 amendment: the
# GOAL text mistakenly used the X channel id as the LinkedIn id) can
# never recur:
#   LinkedIn=68e73facca3a4e6b746d17b4
#   Facebook=68e74731ca3a4e6b746d2469
#   X=68e745c6ca3a4e6b746d22b2
#   org=68e5f2187fe9a5263a3509ab
BUFFER_LINKEDIN_CHANNEL_ID = "68e73facca3a4e6b746d17b4"
BUFFER_ORG_ID = "68e5f2187fe9a5263a3509ab"

# Cross-referenced with orchestrator/dispatch.py's matching literal
# (PV2-03's residual-risk mitigation -- a test in each service asserts the
# two stay equal, see tests/test_agent_name_constant_matches_orchestrator.py).
AGENT_NAME_LOOP_PROOF = "loop-proof-circuit"


def database_url() -> str:
    dsn = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError(
            "neither TEST_DATABASE_URL nor DATABASE_URL is set — Publisher has "
            "no durable store for the jti replay ledger or publish_attempts"
        )
    return dsn


def token_issuer() -> str:
    return os.environ.get("GATE_TOKEN_ISSUER", "cmos-gatekeeper")


def token_audience() -> str:
    return os.environ.get("GATE_TOKEN_AUDIENCE", "cmos-publisher")


def allowed_algorithms() -> tuple[str, ...]:
    raw = os.environ.get("GATE_TOKEN_ALGORITHMS")
    if not raw:
        return DEFAULT_ALLOWED_ALGORITHMS
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def gate_token_public_key_pem() -> str:
    """Public verification key.

    Preferred source is GATE_TOKEN_PUBLIC_KEY_PEM (threaded in as a
    Container Apps secret). Falling back to Key Vault is a lazy import so
    the verifier stays importable without the Azure SDK installed.
    """
    pem = os.environ.get("GATE_TOKEN_PUBLIC_KEY_PEM")
    if pem:
        # Container Apps secret values are base64-encoded on the way in
        # (defends against the "$$" -> "$" collapse); PEM has no "$", but
        # base64 is the established convention for anything threaded
        # through a Container Apps secret, so accept either form.
        if "-----BEGIN" not in pem:
            import base64

            pem = base64.b64decode(pem).decode("utf-8")
        return pem

    vault_url = os.environ.get("KEY_VAULT_URL")
    key_name = os.environ.get("GATE_SIGNING_KEY_NAME", "gate-token-signing-key")
    if not vault_url:
        raise RuntimeError(
            "no gate-token verification key: set GATE_TOKEN_PUBLIC_KEY_PEM or KEY_VAULT_URL"
        )
    return _public_key_pem_from_key_vault(vault_url, key_name)


def _public_key_pem_from_key_vault(vault_url: str, key_name: str) -> str:
    """Fetch the PUBLIC half of the signing key.

    Publisher's managed identity holds a verify/get-capable role only — it
    can read the public key and call verify, and cannot sign (AC-20).
    """
    from azure.identity import DefaultAzureCredential
    from azure.keyvault.keys import KeyClient
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    client = KeyClient(vault_url=vault_url, credential=DefaultAzureCredential())
    jwk = client.get_key(key_name).key
    public_key = rsa.RSAPublicNumbers(
        e=int.from_bytes(jwk.e, "big"), n=int.from_bytes(jwk.n, "big")
    ).public_key()
    return public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
