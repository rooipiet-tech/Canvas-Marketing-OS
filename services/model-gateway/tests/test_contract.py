"""AC-17 — request/response bodies validated against the frozen contract.

Schemas are read straight out of contracts/model-gateway/openapi.yaml; no
copy of them exists in this repository's test code, so a contract change can
never silently pass.

This module is also the artifact a post-deploy job re-runs against the real
endpoint: set GATEWAY_BASE_URL and the conftest `app_client` fixture swaps
the in-process ASGI transport for a network client. Nothing else changes.
"""

from __future__ import annotations

import jsonschema
import yaml
from conftest import OPENAPI_PATH, completion_payload


def _spec() -> dict:
    return yaml.safe_load(OPENAPI_PATH.read_text(encoding="utf-8"))


def _schema(name: str) -> dict:
    return _spec()["components"]["schemas"][name]


def test_completion_request_and_response_match_the_frozen_schemas(app_client, stub_provider):
    payload = completion_payload()
    jsonschema.validate(payload, _schema("CompletionRequest"))

    response = app_client.post("/v1/completions", json=payload)
    assert response.status_code == 200, response.text
    jsonschema.validate(response.json(), _schema("CompletionResponse"))


def test_additive_fields_still_satisfy_the_frozen_request_schema(app_client, stub_provider):
    payload = completion_payload(task_ref="contract-test-1")
    # task_ref/deliberate are additive: no schema in the frozen contract sets
    # additionalProperties: false, so these validate as-is.
    jsonschema.validate(payload, _schema("CompletionRequest"))

    response = app_client.post("/v1/completions", json=payload)
    assert response.status_code == 200
    jsonschema.validate(response.json(), _schema("CompletionResponse"))


def test_error_response_matches_the_frozen_error_schema(app_client, stub_provider):
    response = app_client.post("/v1/completions", json=completion_payload(deliberate=True))
    assert response.status_code == 400
    jsonschema.validate(response.json(), _schema("Error"))


def test_health_matches_the_frozen_inline_schema(app_client):
    health_schema = _spec()["paths"]["/v1/health"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]

    response = app_client.get("/v1/health")
    assert response.status_code == 200
    jsonschema.validate(response.json(), health_schema)
    assert response.json() == {"status": "ok"}
