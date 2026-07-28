"""AGENT-001: all 6 documented GET read paths return 200 application/json
against seeded fixtures."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.clients import get_app_insights_client, get_gatekeeper_client, get_vault_client
from app.clients.gatekeeper_mock import GatekeeperMock
from app.clients.vault_api_mock import VaultApiMock
from app.main import app


class _FakeAppInsightsClient:
    def get_trace_spans(self, task_ref: str, *, timespan_hours: int = 24):
        return [
            {
                "timestamp": "2026-07-28T00:00:00Z",
                "name": "synthetic.root",
                "function_id": "telemetry-lib-selftest",
                "task_ref": task_ref,
                "model": "synthetic",
                "registry_version": "v1",
                "cost": "0.0",
            }
        ]

GET_PATHS = [
    "/tasks",
    "/tasks/smoke-test-1/trace",
    "/approvals",
    "/vault-search",
    "/costs",
    "/kill-switch",
]


@pytest.fixture
def seeded_client():
    vault_mock = VaultApiMock()
    vault_mock.seed_agent_run(agent_name="brief-drafter", status="running")
    vault_mock.seed_asset(vertical="mobility")
    vault_mock.seed_cost(function_id="fn-a", amount="1.00", incurred_at="2026-07-28T00:00:00Z")

    gatekeeper_mock = GatekeeperMock()
    gatekeeper_mock.seed_approval_inbox(status="pending")

    app.dependency_overrides[get_vault_client] = lambda: vault_mock
    app.dependency_overrides[get_gatekeeper_client] = lambda: gatekeeper_mock
    app.dependency_overrides[get_app_insights_client] = lambda: _FakeAppInsightsClient()

    yield TestClient(app)

    app.dependency_overrides.clear()


@pytest.mark.parametrize("path", GET_PATHS)
def test_get_path_returns_json(seeded_client, path: str) -> None:
    response = seeded_client.get(path, headers={"Accept": "application/json"})
    assert response.status_code == 200
    assert "application/json" in response.headers["content-type"]
