"""AC-16 — the additive 'deliberate' hint is flag-gated, never ignored."""

from __future__ import annotations

import config
from conftest import completion_payload


def test_deliberate_true_returns_structured_not_implemented(app_client, stub_provider):
    response = app_client.post("/v1/completions", json=completion_payload(deliberate=True))

    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "NOT_IMPLEMENTED"
    assert stub_provider.call_count == 0


def test_deliberate_false_is_a_normal_completion(app_client, stub_provider):
    response = app_client.post("/v1/completions", json=completion_payload(deliberate=False))
    assert response.status_code == 200
    assert stub_provider.call_count == 1


def test_flipping_the_feature_flag_changes_behaviour(app_client, stub_provider, monkeypatch):
    # Proves the gate is a real flag, not a dead branch: with the flag on,
    # the request is no longer short-circuited.
    monkeypatch.setattr(config, "DELIBERATE_FLAG_ENABLED", True)
    response = app_client.post("/v1/completions", json=completion_payload(deliberate=True))
    assert response.status_code == 200
    assert stub_provider.call_count == 1
