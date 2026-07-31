"""INTEG-001: VaultApiMock seed/list roundtrip; get_vault_client() mode
selection; Cost amount is decimal.Decimal end to end."""

from __future__ import annotations

import decimal

import pytest

from app.clients.vault_api_mock import VaultApiMock
from app.clients.vault_api_real import VaultApiHttpClient


@pytest.mark.asyncio
async def test_seed_and_list_agent_run() -> None:
    mock = VaultApiMock()
    mock.seed_agent_run(agent_name="brief-drafter", status="running")
    runs = await mock.list_agent_runs()
    assert len(runs) == 1
    assert runs[0]["agent_name"] == "brief-drafter"
    assert runs[0]["status"] == "running"


def test_get_vault_client_mode_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VAULT_API_MODE", raising=False)
    from app.clients import get_vault_client
    from app.clients.vault_api_mock import VaultApiMock as MockClass

    assert isinstance(get_vault_client(), MockClass)

    monkeypatch.setenv("VAULT_API_MODE", "real")
    assert isinstance(get_vault_client(), VaultApiHttpClient)

    monkeypatch.setenv("VAULT_API_MODE", "mock")


@pytest.mark.asyncio
async def test_seeded_cost_amount_is_decimal() -> None:
    mock = VaultApiMock()
    seeded = mock.seed_cost(agent_run_id="run-1", provider="anthropic", amount="12.50")
    assert type(seeded["amount"]) is decimal.Decimal
    costs = await mock.list_costs()
    assert type(costs[0]["amount"]) is decimal.Decimal
