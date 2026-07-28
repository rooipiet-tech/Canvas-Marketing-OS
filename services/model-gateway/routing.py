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


def known_models() -> list[str]:
    """Every logical model id policy/routing.yaml configures, sorted."""
    return sorted(_load())


def unknown_model_message(known: list[str] | None = None) -> str:
    """The caller-facing text for an unresolvable model id.

    DR-5 — BUILT FROM CONFIG-SIDE FACTS ONLY. The submitted ``model`` value is
    caller-controlled (the frozen contract constrains it to ``type: string``,
    with no enum) and routing runs at step 2 of completion.py, BEFORE the
    redaction firewall has scanned anything and without writing a
    gate_decisions audit row. Interpolating it into this message would echo a
    caller's personal information straight back out, unscanned and unaudited —
    the same class of leak DR-3 fixed on the shape-validation path. The only
    thing named here is what policy/routing.yaml itself configures.
    """
    names = known_models() if known is None else list(known)
    return "unknown model identifier; configured models: " + (", ".join(names) or "<none>")


class UnknownModelError(ValueError):
    """Raised by :func:`resolve` for a model id routing.yaml does not define.

    ``str(self)`` is deliberately the value-free message: whoever catches this
    — now or later — cannot leak the submitted identifier by the obvious
    reflex of putting ``str(exc)`` on the wire or in an audit row. The raw
    value is kept on ``.model`` for programmatic use only; nothing in this
    service interpolates it into a message. It is not needed for diagnosis
    either: completion.py's per-request JSON log line already records the
    submitted ``model`` server-side.
    """

    def __init__(self, model: str, known: list[str]):
        self.model = model
        self.known = list(known)
        super().__init__(unknown_model_message(self.known))


def resolve(model: str) -> RouteDecision:
    """Resolve a logical model id, raising UnknownModelError if it is unknown."""
    routes = _load()
    try:
        return routes[model]
    except KeyError:
        raise UnknownModelError(model, sorted(routes)) from None


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
