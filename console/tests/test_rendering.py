"""CONSOLE-001/AGENT-001 (step 9): /tasks renders HTML by default and JSON
on Accept: application/json; decimal_json_encoder produces a fixed-format
string, never Python float repr."""

from __future__ import annotations

from decimal import Decimal

from fastapi.testclient import TestClient

from app.clients import get_vault_client
from app.clients.vault_api_mock import VaultApiMock
from app.main import app
from app.rendering import decimal_json_encoder

AUTH_HEADERS = {
    "X-MS-CLIENT-PRINCIPAL-ID": "operator-1",
    "X-MS-CLIENT-PRINCIPAL-NAME": "operator@example.com",
}


def test_decimal_json_encoder_is_fixed_format() -> None:
    assert decimal_json_encoder(Decimal("12.50")) == "12.50"
    assert decimal_json_encoder(Decimal("0.1")) == "0.1"


def test_tasks_html_default_accept() -> None:
    mock = VaultApiMock()
    mock.seed_agent_run(agent_name="brief-drafter", status="running")
    app.dependency_overrides[get_vault_client] = lambda: mock

    client = TestClient(app)
    response = client.get("/tasks", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "<table" in response.text

    app.dependency_overrides.clear()


def test_tasks_json_accept() -> None:
    mock = VaultApiMock()
    mock.seed_agent_run(agent_name="brief-drafter", status="running")
    app.dependency_overrides[get_vault_client] = lambda: mock

    client = TestClient(app)
    response = client.get("/tasks", headers={"Accept": "application/json", **AUTH_HEADERS})
    assert response.status_code == 200
    assert "application/json" in response.headers["content-type"]
    body = response.json()
    agent_names = [row["agent_name"] for row in body["rows"]]
    statuses = [row["status"] for row in body["rows"]]
    assert "brief-drafter" in agent_names
    assert "running" in statuses

    app.dependency_overrides.clear()
