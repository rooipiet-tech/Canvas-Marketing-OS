"""AC-09 — three logical models resolve to three distinct risk tiers."""

from __future__ import annotations

import pytest
import routing
import yaml
from conftest import REDACTION_RULES_PATH, completion_payload

LOGICAL_MODELS = ("claude-opus", "claude-sonnet", "claude-haiku")


def _fixtures() -> dict:
    return yaml.safe_load(REDACTION_RULES_PATH.read_text(encoding="utf-8"))["fixtures"]


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


def test_unknown_model_400_never_echoes_the_submitted_identifier(
    app_client, fake_repo, stub_provider
):
    """DR-5: the same leak class DR-3 closed, in routing's sibling error path.

    `model` is caller-controlled — the frozen contract types it `string` with
    no enum — and routing resolution runs at step 2, BEFORE the redaction
    firewall scans anything and without writing any gate_decisions row. So a
    message built with the submitted value (the old
    `unknown model {model!r} ...`) reflected whatever a caller put there
    straight back out, unscanned and unaudited. Asserted against the raw
    response text so no nesting or escaping can hide a leak.
    """
    client_name = _fixtures()["client_names"][0]
    email = _fixtures()["emails"][0]
    smuggled = f"{client_name} <{email}>"

    response = app_client.post("/v1/completions", json=completion_payload(model=smuggled))

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "UNKNOWN_MODEL"
    assert stub_provider.call_count == 0

    raw = response.text
    assert smuggled not in raw
    assert client_name not in raw
    assert email not in raw
    assert "example.co.za" not in raw
    # There is no audit row on this path to leak into either — which is
    # precisely why the response body has to be clean on its own.
    assert fake_repo.gate_decisions.rows == []

    # ...while staying actionable: it names what routing.yaml configures.
    message = response.json()["error"]["message"]
    for known in LOGICAL_MODELS:
        assert known in message


def test_unknown_model_error_str_is_value_free_by_construction():
    """Belt-and-braces: even a future caller that reaches for the reflexive
    `str(exc)` cannot leak the submitted identifier."""
    smuggled = _fixtures()["client_names"][0]

    with pytest.raises(routing.UnknownModelError) as caught:
        routing.resolve(smuggled)

    assert smuggled not in str(caught.value)
    assert smuggled not in repr(caught.value.args)
    # The raw value stays reachable programmatically, just never in a message.
    assert caught.value.model == smuggled
