"""Live upstream smoke test — with a clean skip when there is no vault reach.

Run as a script (``python live_smoke.py``) or call ``run_live_smoke()``.

Provider-agnostic by construction: the adapter is resolved through
routing.yaml + the provider registry, exactly like a real request, so this
script names no vendor and keeps working when the haiku-class tier is
re-pointed at a different provider.

The upstream credential arrives in the process environment via the Container
App's Key Vault secret reference. Outside that network context — a laptop, a
GitHub-hosted runner, any CI sandbox — the vault is unreachable by design
(publicNetworkAccess is Disabled), so the credential is simply absent. That
case must exit 0 with an explicit skip message: it is an expected
environment, not a failure, and never a hang.

The real end-to-end smoke test therefore runs in-VNet, as a one-shot
Container Apps Job (caj-gateway-smoke) mirroring the caj-vault-query
pattern — see .github/workflows/deploy-gateway.yml.
"""

from __future__ import annotations

import asyncio
import os
import sys

import routing
from providers import registry

SKIP_MESSAGE = "skipped: no vault reach"

# Cheapest tier is enough to prove reachability end to end.
SMOKE_TIER = "haiku"
SMOKE_PROMPT = "reply with the single word: ok"


def resolve_smoke_provider():
    """Return (route, provider instance) for the smoke tier."""
    route = routing.resolve_by_tier(SMOKE_TIER)
    return route, registry.get_provider(route.provider)


def credential_env_name() -> str:
    """The environment variable the resolved adapter reads its key from."""
    _, provider = resolve_smoke_provider()
    return str(getattr(provider, "API_KEY_ENV", "") or "")


def has_credential() -> bool:
    name = credential_env_name()
    return bool(name and (os.environ.get(name) or "").strip())


def run_live_smoke() -> int:
    """Return 0 on success or clean skip, non-zero only on a real failure."""
    if not has_credential():
        print(f"{SKIP_MESSAGE} (no upstream credential in this environment)")
        return 0

    route, provider = resolve_smoke_provider()
    try:
        result = asyncio.run(
            provider.complete(
                provider_model=route.provider_model,
                messages=[{"role": "user", "content": SMOKE_PROMPT}],
                max_tokens=16,
                temperature=0.0,
                tools=None,
            )
        )
    except Exception as exc:  # noqa: BLE001 - report, do not raise, from a script
        print(f"live smoke failed: {exc}")
        return 1

    print(
        f"live smoke ok via {route.provider}: {len(result.content)} chars, "
        f"{result.input_tokens} in / {result.output_tokens} out"
    )
    return 0


if __name__ == "__main__":
    sys.exit(run_live_smoke())
