"""AGENT-001: all 6 documented GET read paths return 200 application/json
against seeded fixtures."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.clients import get_app_insights_client, get_gatekeeper_client, get_vault_client
from app.clients.app_insights_client import build_trace_query
from app.clients.gatekeeper_mock import GatekeeperMock
from app.clients.vault_api_mock import VaultApiMock
from app.main import app

AUTH_HEADERS = {
    "X-MS-CLIENT-PRINCIPAL-ID": "operator-1",
    "X-MS-CLIENT-PRINCIPAL-NAME": "operator@example.com",
}


class _FakeAppInsightsClient:
    def get_trace_spans(self, task_ref: str, *, timespan_hours: int = 24):
        # Route the fake through the SAME allowlist validation the real
        # AppInsightsClient.get_trace_spans applies via build_trace_query,
        # so route-level tests (POLISH-004's 400-vs-graceful-degrade split,
        # RISK-001) exercise real validation behavior without any network
        # dependency.
        build_trace_query(task_ref)
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
    response = seeded_client.get(
        path, headers={"Accept": "application/json", **AUTH_HEADERS}
    )
    assert response.status_code == 200
    assert "application/json" in response.headers["content-type"]


@pytest.mark.parametrize("path", GET_PATHS)
def test_get_path_without_auth_headers_returns_401(seeded_client, path: str) -> None:
    """RISK-003: a code-level backstop on every GET route rejects a request
    with no Easy-Auth principal headers, independent of the Bicep-wired
    Container Apps Easy Auth ingress layer."""
    response = seeded_client.get(path, headers={"Accept": "application/json"})
    assert response.status_code == 401


def test_trace_route_rejects_invalid_task_ref_with_400(seeded_client) -> None:
    """RISK-001: an invalid task_ref is a 400 client error, distinct from
    POLISH-004's graceful-empty-state handling of a genuine query failure."""
    response = seeded_client.get(
        "/tasks/not valid'/trace", headers={"Accept": "application/json", **AUTH_HEADERS}
    )
    assert response.status_code == 400


def test_trace_route_degrades_gracefully_on_query_failure() -> None:
    """POLISH-004: a genuine Application Insights query failure (e.g. no
    reachable App Insights resource) renders the empty-state pattern, not a
    raw 500."""

    class _FailingAppInsightsClient:
        def get_trace_spans(self, task_ref: str, *, timespan_hours: int = 24):
            raise RuntimeError("simulated Application Insights connectivity failure")

    app.dependency_overrides[get_app_insights_client] = lambda: _FailingAppInsightsClient()
    client = TestClient(app)

    response = client.get(
        "/tasks/smoke-test-1/trace", headers={"Accept": "application/json", **AUTH_HEADERS}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["query_failed"] is True
    assert body["rows"] == []

    html_response = client.get("/tasks/smoke-test-1/trace", headers=AUTH_HEADERS)
    assert html_response.status_code == 200
    assert "temporarily unavailable" in html_response.text

    app.dependency_overrides.clear()


def test_root_redirects_authenticated_request_to_tasks(seeded_client) -> None:
    """Live-reported 2026-08-01: an authenticated operator landing on the
    console's own root FQDN got a bare framework 404 — nothing handled
    GET / at all. Root now redirects to the documented landing screen."""
    response = seeded_client.get("/", headers=AUTH_HEADERS, follow_redirects=False)
    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/tasks"


def test_root_without_auth_headers_returns_401(seeded_client) -> None:
    """RISK-003: same code-level auth backstop as every other route — an
    unauthenticated request gets 401, never a redirect."""
    response = seeded_client.get("/", follow_redirects=False)
    assert response.status_code == 401
