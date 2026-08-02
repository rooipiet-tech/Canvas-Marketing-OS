"""mcp_common.credentials — dual-mode secret resolution (AC-4, AC-6, AC-7).

Fixture vs. live mode is selected PURELY by whether a server's secret env
var is set — no code or config file edit is needed to switch modes.
Resolution order for `resolve_secret`:

  0. If a caller has explicitly requested fixture mode for the current
     request (see `force_fixture_mode` below), skip straight to fixture
     mode — before even looking at env vars or Key Vault.
  1. The secret's own env var (e.g. BUFFER_API_KEY, CANVA_CLIENT_ID). This
     is how the deployed Container App receives the value (via a Key-Vault
     -backed Container Apps secretRef wired in infra/modules/mcp — see
     container-app.bicep), and also how local/CI testing sets a dummy
     value to exercise the live code path (AC-4/AC-6).
  2. If unset, and KEY_VAULT_URI is also set, attempt a direct Key Vault
     SDK lookup (azure-identity + azure-keyvault-secrets) as a fallback
     resolution path. ANY failure here (auth, network, missing secret) is
     treated identically to "absent" — this function never raises — so a
     live smoke test run with no credentials configured never attempts a
     network call (AC-6) and always falls through cleanly to fixture mode.
  3. Otherwise: None (fixture mode).

Real vault secret names used by this build (per environment facts, not
docs/credentials-runbook.md's stale names): buffer-api-key,
canva-client-id, canva-client-secret. mcp-web has no vendor credential at
all (it is a fetch+rate-limit server, not a Buffer/Canva-style
integration) — its dual-mode gate is the non-secret MCP_WEB_LIVE_MODE flag
via `flag_enabled` below, per the orchestrator-approved waiver recorded in
.loop/spec.json's amendments (v2, addressing plan-reviewer finding F3).

INCIDENT (2026-08-02, live — caj-mcp-smoke, deploy-mcp #26 attempt 2):
tests/test_conformance.py's own docstring states its AC-1 contract is
"fixture mode... with no live secrets present" — using deliberately
synthetic tool arguments (SYNTH-TEMPLATE-0001, example.com/synthetic)
never meant to reach a real vendor. Once buffer-api-key/canva-client-id/
canva-client-secret landed in Key Vault (a separate, unrelated human
errand, same day) AND ca-mcp-web's MCP_WEB_LIVE_MODE flag was set, all 3
servers correctly followed their documented dual-mode contract and
started resolving real credentials — this is the SYSTEM WORKING AS
DESIGNED, not a bug in resolve_secret/is_live_mode/flag_enabled
themselves. But it meant caj-mcp-smoke's protocol-conformance check
started making REAL Buffer/Canva API calls with synthetic garbage
arguments, an uncontrolled side effect a pure protocol-shape smoke test
was never designed to have. The fix is not to loosen AC-1's assertion to
accept live mode (that would stop verifying fixture-mode conformance at
all, and does nothing about the uncontrolled live vendor calls) — it's to
give a caller an explicit, opt-in, per-request way to require fixture
mode regardless of ambient credential state, so real production traffic
(orchestrator's real ingest-signals calls, etc.) keeps automatically
going live the moment credentials exist (the documented, desired
behavior), while the conformance smoke can deterministically request the
one thing it actually contracts to test. See protocol.py's `_mcp_endpoint`
for where this is set from an inbound request header.
"""

from __future__ import annotations

import contextvars
import os
from contextlib import contextmanager
from typing import Iterator

# Request-scoped, not global/process-wide: set for the duration of one
# POST /mcp handling (see protocol.py's _mcp_endpoint) and always reset
# afterward via the context manager below, so it can never leak across
# requests or accidentally suppress live mode for unrelated traffic.
_force_fixture_mode: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "mcp_common_force_fixture_mode", default=False
)


@contextmanager
def force_fixture_mode(active: bool) -> Iterator[None]:
    """Context manager: while active, resolve_secret()/flag_enabled() both
    behave exactly as if no credential/flag were ever configured, no
    matter what's actually in the environment or Key Vault. Opt-in and
    request-scoped by design — this must never become a permanent,
    server-wide override, since that would defeat the documented "goes
    live the moment real credentials exist" behavior for real traffic."""
    token = _force_fixture_mode.set(active)
    try:
        yield
    finally:
        _force_fixture_mode.reset(token)


def resolve_secret(env_var_name: str, vault_secret_name: str | None = None) -> str | None:
    """Resolve a secret's value, or None if absent (fixture mode)."""
    if _force_fixture_mode.get():
        return None

    value = os.environ.get(env_var_name)
    if value:
        return value

    vault_uri = os.environ.get("KEY_VAULT_URI")
    if not vault_secret_name or not vault_uri:
        return None

    try:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient

        credential = DefaultAzureCredential()
        client = SecretClient(vault_url=vault_uri, credential=credential)
        secret = client.get_secret(vault_secret_name)
        return secret.value
    except Exception:
        # Never raise: absence of a reachable Key Vault must degrade to
        # fixture mode, not an error, per AC-6.
        return None


def is_live_mode(env_var_name: str, vault_secret_name: str | None = None) -> bool:
    """True iff a real (non-empty) secret value is resolvable."""
    return resolve_secret(env_var_name, vault_secret_name) is not None


def flag_enabled(env_var_name: str) -> bool:
    """Non-secret feature-flag equivalent of is_live_mode, for mcp-web.

    mcp-web has no vendor credential to gate on, so its fixture-vs-live
    switch is a plain boolean env var (MCP_WEB_LIVE_MODE) rather than a
    Key-Vault-backed secret. Truthy values: 1, true, yes, on
    (case-insensitive); anything else (including absent) is falsy.
    """
    if _force_fixture_mode.get():
        return False

    value = os.environ.get(env_var_name, "").strip().lower()
    return value in ("1", "true", "yes", "on")
