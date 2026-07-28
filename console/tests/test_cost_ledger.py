"""CONSOLE-004 / GOAL-002: cost ledger groups by function and by day using
Decimal arithmetic; JSON amounts are decimal_json_encoder strings."""

from __future__ import annotations

from decimal import Decimal

from fastapi.testclient import TestClient

from app.clients import get_vault_client
from app.clients.vault_api_mock import VaultApiMock
from app.main import app

DAY = "2026-07-28"


def _seed_five_fixtures(mock: VaultApiMock) -> None:
    mock.seed_cost(
        agent_run_id="run-1",
        function_id="fn-a",
        amount="1.10",
        incurred_at=f"{DAY}T01:00:00Z",
    )
    mock.seed_cost(
        agent_run_id="run-1",
        function_id="fn-a",
        amount="2.20",
        incurred_at=f"{DAY}T02:00:00Z",
    )
    mock.seed_cost(
        agent_run_id="run-2",
        function_id="fn-b",
        amount="3.30",
        incurred_at=f"{DAY}T03:00:00Z",
    )
    mock.seed_cost(
        agent_run_id="run-2",
        function_id="fn-b",
        amount="4.40",
        incurred_at=f"{DAY}T04:00:00Z",
    )
    mock.seed_cost(
        agent_run_id="run-1",
        function_id="fn-a",
        amount="5.50",
        incurred_at=f"{DAY}T05:00:00Z",
    )


def test_costs_group_by_function_and_day_match_hand_computed_sums() -> None:
    mock = VaultApiMock()
    _seed_five_fixtures(mock)
    app.dependency_overrides[get_vault_client] = lambda: mock

    client = TestClient(app)

    by_function = client.get(
        "/costs?group_by=function", headers={"Accept": "application/json"}
    ).json()
    totals_by_function = {row["group_key"]: Decimal(row["total"]) for row in by_function["rows"]}
    assert totals_by_function["fn-a"] == Decimal("1.10") + Decimal("2.20") + Decimal("5.50")
    assert totals_by_function["fn-b"] == Decimal("3.30") + Decimal("4.40")

    by_day = client.get(
        f"/costs?group_by=day&date={DAY}", headers={"Accept": "application/json"}
    ).json()
    totals_by_day = {row["group_key"]: Decimal(row["total"]) for row in by_day["rows"]}
    assert totals_by_day[DAY] == Decimal("1.10") + Decimal("2.20") + Decimal("3.30") + Decimal(
        "4.40"
    ) + Decimal("5.50")

    app.dependency_overrides.clear()


def test_costs_json_amount_is_fixed_format_string_not_float() -> None:
    mock = VaultApiMock()
    mock.seed_cost(function_id="fn-a", amount="12.50", incurred_at=f"{DAY}T00:00:00Z")
    app.dependency_overrides[get_vault_client] = lambda: mock

    client = TestClient(app)
    response = client.get("/costs?group_by=function", headers={"Accept": "application/json"})
    body = response.json()
    total_value = body["rows"][0]["total"]
    assert isinstance(total_value, str)
    assert total_value == "12.50"

    app.dependency_overrides.clear()
