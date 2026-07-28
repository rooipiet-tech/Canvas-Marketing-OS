"""AC-09 — three logical models resolve to three distinct risk tiers."""

from __future__ import annotations

from conftest import completion_payload

LOGICAL_MODELS = ("claude-opus", "claude-sonnet", "claude-haiku")


def test_three_logical_models_resolve_to_three_distinct_tiers(app_client, stub_provider):
    tiers = []
    provider_models = []
    for model in LOGICAL_MODELS:
        response = app_client.post("/v1/completions", json=completion_payload(model=model))
        assert response.status_code == 200, response.text
        tiers.append(response.json()["routing_tier"])
        provider_models.append(stub_provider.last_call_kwargs["provider_model"])

    assert set(tiers) == {'opus', 'sonnet', 'haiku'}
    assert len(set(provider_models)) == 3
    assert stub_provider.call_count == 3


def test_unknown_logical_model_is_rejected(app_client, stub_provider):
    response = app_client.post(
        "/v1/completions", json=completion_payload(model="no-such-model")
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "UNKNOWN_MODEL"
    assert stub_provider.call_count == 0
