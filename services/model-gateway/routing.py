"""Logical-model -> (tier, provider, provider model) resolution.

Policy lives in policy/routing.yaml as plain data; this module is just the
lookup. It never names a vendor: whatever string the YAML records under
``provider`` is handed to providers.registry.get_provider().
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

ROUTING_POLICY_PATH = Path(__file__).resolve().parent / "policy" / "routing.yaml"

_routes: dict[str, "RouteDecision"] | None = None


@dataclass(frozen=True)
class RouteDecision:
    """A resolved route for one request."""

    model: str
    tier: str
    provider: str
    provider_model: str


def _load() -> dict[str, RouteDecision]:
    global _routes
    if _routes is None:
        document = yaml.safe_load(ROUTING_POLICY_PATH.read_text(encoding="utf-8")) or {}
        entries = document.get("models") or document.get("routes") or {}
        _routes = {
            name: RouteDecision(
                model=name,
                tier=str(entry["tier"]),
                provider=str(entry["provider"]),
                provider_model=str(entry["provider_model"]),
            )
            for name, entry in entries.items()
        }
    return _routes


def resolve(model: str) -> RouteDecision:
    """Resolve a logical model id, raising ValueError if it is unknown."""
    routes = _load()
    try:
        return routes[model]
    except KeyError:
        known = ", ".join(sorted(routes)) or "<none>"
        raise ValueError(f"unknown model {model!r} (configured: {known})") from None


def resolve_by_tier(tier: str) -> RouteDecision:
    """Resolve any route for a risk tier, deterministically.

    Used by the budget soft-breach downgrade path: 'give me a model of tier
    X'. Picks the alphabetically-first logical model id for that tier so the
    choice is stable across processes and restarts.
    """
    routes = _load()
    for name in sorted(routes):
        if routes[name].tier == tier:
            return routes[name]
    raise ValueError(f"no route configured for tier {tier!r}")


def add_route(model: str, tier: str, provider: str, provider_model: str) -> None:
    """Merge one route into the in-memory table (test hook).

    Deliberately does not write to routing.yaml: this is how a test can point
    a new logical model at a newly registered provider without editing any
    policy file or any router/caller module.
    """
    routes = _load()
    routes[model] = RouteDecision(
        model=model, tier=tier, provider=provider, provider_model=provider_model
    )


def reset_routes() -> None:
    """Drop the in-memory table so the next resolve() reloads from YAML."""
    global _routes
    _routes = None
