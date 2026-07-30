"""Gatekeeper runtime configuration, read from the process environment.

Every value is resolved lazily on each call rather than snapshotted at
import time, so tests (and a running container whose env is re-read after
a revision restart) always see the current value.
"""

from __future__ import annotations

import os
from pathlib import Path

# Repo-relative default for the autonomy policy file. In the container the
# service root is /app, so policy/autonomy.yaml resolves the same way.
SERVICE_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_POLICY_PATH = SERVICE_ROOT / "policy" / "autonomy.yaml"

# Approval deep-links expire 24h after issuance (AC-33).
APPROVAL_LINK_TTL_SECONDS = 24 * 60 * 60

# Gate tokens are deliberately short-lived; exp is a hard contract claim.
DEFAULT_GATE_TOKEN_TTL_SECONDS = 900


def database_url() -> str:
    """Postgres DSN. TEST_DATABASE_URL wins so test runs cannot touch prod."""
    dsn = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError(
            "neither TEST_DATABASE_URL nor DATABASE_URL is set — Gatekeeper "
            "has no durable store for gate decisions, kill switches or the "
            "approval inbox"
        )
    return dsn


def policy_path() -> Path:
    return Path(os.environ.get("AUTONOMY_POLICY_PATH", str(DEFAULT_POLICY_PATH)))


def signer_backend() -> str:
    """'local' (test-only LocalRSASigner) or 'keyvault' (production)."""
    return os.environ.get("SIGNER_BACKEND", "local").strip().lower()


def key_vault_url() -> str | None:
    return os.environ.get("KEY_VAULT_URL") or None


def signing_key_name() -> str:
    return os.environ.get("GATE_SIGNING_KEY_NAME", "gate-token-signing-key")


def local_signing_key_pem() -> str | None:
    """PEM for the test-only local signer. Absent => ephemeral key."""
    return os.environ.get("GATE_SIGNING_PRIVATE_KEY_PEM") or None


def token_issuer() -> str:
    return os.environ.get("GATE_TOKEN_ISSUER", "cmos-gatekeeper")


def token_audience() -> str:
    return os.environ.get("GATE_TOKEN_AUDIENCE", "cmos-publisher")


def token_ttl_seconds() -> int:
    return int(os.environ.get("GATE_TOKEN_TTL_SECONDS", DEFAULT_GATE_TOKEN_TTL_SECONDS))


def teams_webhook_url() -> str | None:
    """Workflows-based Teams webhook URL, or None (the current real state).

    Absent => approval requests fall back to the local approval inbox,
    which is the primary end-to-end-tested mode this session.
    """
    return os.environ.get("TEAMS_WEBHOOK_URL") or None


def approval_base_url() -> str:
    """Public base URL of the Entra-ID-protected approval-action app."""
    return os.environ.get("APPROVAL_BASE_URL", "https://approval.invalid").rstrip("/")


def approval_link_ttl_seconds() -> int:
    return int(os.environ.get("APPROVAL_LINK_TTL_SECONDS", APPROVAL_LINK_TTL_SECONDS))
