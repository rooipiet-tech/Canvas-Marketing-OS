"""Live Azure Container App FQDN resolution (AC-19, L-0025).

Canonical implementation (TD-16). Before this package existed,
`resolve_live_fqdn` via `az containerapp show` was implemented
independently, byte-near-identically, in three places:

  * services/orchestrator/orchestrator/clients/azure_fqdn.py -- generalised
    across orchestrator's five HTTP clients (gateway/vault/gatekeeper/mcp/
    publisher), and the only one of the three with direct unit test
    coverage (test_resolve_live_fqdn_never_raises_when_az_unavailable).
  * services/registry/gateway_client.py's resolve_live_gateway_fqdn --
    hardcoded to ca-model-gateway, no test coverage of its own.
  * services/publisher/app/buffer_client.py's own resolve_live_fqdn --
    hardcoded to mcp-buffer, no test coverage of its own.

This module carries orchestrator's copy forward unchanged (never a guessed
hostname; None on any failure, never a fabricated fallback), generalised
to any (resource_group, app_name) pair the way orchestrator's already was.
"""

from __future__ import annotations

import subprocess

DEFAULT_RESOURCE_GROUP = "cmos-dev"


def resolve_live_fqdn(
    app_name: str, *, resource_group: str = DEFAULT_RESOURCE_GROUP, timeout: float = 15.0
) -> str | None:
    """Resolve `app_name`'s real live FQDN via `az containerapp show`.

    Returns an `https://` URL, or None on any failure (never a fabricated
    fallback — L-0025): the Azure CLI is unavailable, the caller isn't
    logged in, the app doesn't exist yet, or the lookup times out.
    """
    try:
        result = subprocess.run(
            [
                "az",
                "containerapp",
                "show",
                "-g",
                resource_group,
                "-n",
                app_name,
                "--query",
                "properties.configuration.ingress.fqdn",
                "-o",
                "tsv",
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    fqdn = result.stdout.strip()
    if result.returncode != 0 or not fqdn:
        return None
    return f"https://{fqdn}"
