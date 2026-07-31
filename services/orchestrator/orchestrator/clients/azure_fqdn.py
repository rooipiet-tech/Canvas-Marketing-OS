"""Live Container App FQDN resolution (AC-19, L-0025).

Mirrors services/registry/gateway_client.py's resolve_live_gateway_fqdn
exactly, generalised to any (resource_group, app_name) pair so
gateway_client.py / vault_client_ext.py / gatekeeper_client.py share one
implementation instead of three copies. Never guesses or falls back to a
hardcoded hostname: returns the resource's actual
`properties.configuration.ingress.fqdn`, or None if the Azure CLI is
unavailable, the caller isn't logged in, the app doesn't exist yet, or the
lookup times out.
"""

from __future__ import annotations

import subprocess

DEFAULT_RESOURCE_GROUP = "cmos-dev"


def resolve_live_fqdn(
    app_name: str, *, resource_group: str = DEFAULT_RESOURCE_GROUP, timeout: float = 15.0
) -> str | None:
    """Resolve `app_name`'s real live FQDN via `az containerapp show`.

    Returns an `https://` URL, or None on any failure (never a fabricated
    fallback — L-0025).
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
