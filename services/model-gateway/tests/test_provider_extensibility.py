"""AC-07 — a second adapter plugs in with zero router/caller edits.

The only things this test touches are the registry and the routing table
(both data). It deliberately does NOT monkeypatch main.py or completion.py
internals: if dispatch needed a code change to reach a new provider, this
test would fail.
"""

from __future__ import annotations

import routing
from conftest import completion_payload
from providers import registry
from providers.base import Provider, ProviderResult


class SecondProvider(Provider):
    """A second, entirely different adapter implementing the base interface."""

    calls: list[dict] = []

    async def complete(self, **kwargs) -> ProviderResult:
        SecondProvider.calls.append(kwargs)
        return ProviderResult(content="second adapter answered", input_tokens=4, output_tokens=2)


def test_second_provider_dispatches_via_registry_only(app_client):
    SecondProvider.calls.clear()

    # 1. register the new adapter under a new name...
    registry.register("second-provider", SecondProvider)
    # 2. ...and point a logical model at it, routing.yaml-shaped.
    routing.add_route(
        "test-extend-model",
        tier="haiku",
        provider="second-provider",
        provider_model="second-1",
    )

    response = app_client.post(
        "/v1/completions", json=completion_payload(model="test-extend-model")
    )

    assert response.status_code == 200
    body = response.json()
    assert body["content"] == "second adapter answered"
    assert body["routing_tier"] == "haiku"
    assert len(SecondProvider.calls) == 1
    assert SecondProvider.calls[0]["provider_model"] == "second-1"


def test_registry_round_trip_returns_the_registered_class():
    registry.register("second-provider", SecondProvider)
    assert isinstance(registry.get_provider("second-provider"), SecondProvider)
