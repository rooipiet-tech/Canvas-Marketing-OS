"""AC-17 — request/response bodies validated against the frozen contract.

Schemas are read straight out of contracts/model-gateway/openapi.yaml; no
copy of them exists in this repository's test code, so a contract change can
never silently pass.

This module is also the artifact a post-deploy job re-runs against the real
endpoint: set GATEWAY_BASE_URL and the conftest `app_client` fixture swaps
the in-process ASGI transport for a network client. Nothing else changes.
"""

from __future__ import annotations

import db
import httpx
import jsonschema
import yaml
from conftest import OPENAPI_PATH, completion_payload, run
from main import app


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


def test_malformed_request_is_rejected_against_the_frozen_request_schema(
    app_client, stub_provider
):
    # `content` is typed `string` in the frozen contract; anything else is a
    # client error, caught before the provider adapter is ever reached.
    payload = completion_payload()
    payload["messages"] = [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]

    response = app_client.post("/v1/completions", json=payload)

    assert response.status_code == 400
    jsonschema.validate(response.json(), _schema("Error"))
    assert response.json()["error"]["code"] == "INVALID_REQUEST"
    assert stub_provider.call_count == 0


def test_upstream_provider_failure_returns_the_frozen_error_schema(app_client, stub_provider):
    """The frozen contract documents 500 as an Error body, so an upstream
    non-2xx (provider adapter raise_for_status) must not leak a bare 500."""

    request = httpx.Request("POST", "https://provider.invalid/v1/messages")

    async def _raise_upstream(**kwargs):
        raise httpx.HTTPStatusError(
            "overloaded", request=request, response=httpx.Response(529, request=request)
        )

    stub_provider.complete = _raise_upstream

    response = app_client.post("/v1/completions", json=completion_payload())

    assert response.status_code == 500, response.text
    jsonschema.validate(response.json(), _schema("Error"))
    assert response.json()["error"]["code"] == "PROVIDER_ERROR"


def test_unexpected_exception_returns_the_frozen_error_schema(fake_repo, stub_provider):
    """An unexpected failure (here: the Vault repository blowing up) must also
    answer with the frozen Error schema, not Starlette's plain-text 500.

    This one builds its own transport with raise_app_exceptions=False: for a
    handler registered against `Exception`, Starlette's ServerErrorMiddleware
    sends the response and *then* re-raises so a real server can log it. A
    live uvicorn deployment therefore returns this exact body to the caller;
    the test client just has to not treat the re-raise as a failure.
    """

    async def _boom(agent_run_id):
        raise RuntimeError("vault unreachable")

    fake_repo.get_agent_name = _boom
    app.dependency_overrides[db.get_repository] = lambda: fake_repo
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://testserver",
        timeout=30.0,
    )
    try:
        response = run(client.post("/v1/completions", json=completion_payload()))
    finally:
        run(client.aclose())
        app.dependency_overrides.clear()

    assert response.status_code == 500, response.text
    jsonschema.validate(response.json(), _schema("Error"))
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"


def test_health_matches_the_frozen_inline_schema(app_client):
    health_schema = _spec()["paths"]["/v1/health"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]

    response = app_client.get("/v1/health")
    assert response.status_code == 200
    jsonschema.validate(response.json(), health_schema)
    assert response.json() == {"status": "ok"}
