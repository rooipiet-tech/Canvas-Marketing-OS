"""Name-keyed provider registry — the only place a vendor name is bound.

Request-handling code (completion.py) asks for a provider by the name that
policy/routing.yaml records for a logical model. Nothing on the completion
path knows which vendor that resolves to, which is what makes a second
adapter a data change plus one ``register()`` call.
"""

from __future__ import annotations

from providers.anthropic import AnthropicProvider
from providers.base import Provider

DEFAULT_PROVIDER_NAME = "anthropic"

PROVIDERS: dict[str, type[Provider]] = {}


def register(name: str, cls: type[Provider]) -> None:
    """Bind (or rebind) a provider name to an adapter class."""
    PROVIDERS[name] = cls


def get_provider(name: str) -> Provider:
    """Instantiate the adapter registered under ``name``."""
    try:
        cls = PROVIDERS[name]
    except KeyError:
        known = ", ".join(sorted(PROVIDERS)) or "<none>"
        raise ValueError(f"unknown provider {name!r} (registered: {known})") from None
    return cls()


def reset_default_providers() -> None:
    """Restore the built-in registrations only (test-teardown hook)."""
    PROVIDERS.clear()
    register(DEFAULT_PROVIDER_NAME, AnthropicProvider)


reset_default_providers()
