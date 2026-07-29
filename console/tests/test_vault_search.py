"""CONSOLE-003: /vault-search filters client-side by taxonomy dimension;
the mock's list_assets() call receives no vertical param."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.clients import get_vault_client
from app.clients.vault_api_mock import VaultApiMock
from app.main import app

AUTH_HEADERS = {
    "X-MS-CLIENT-PRINCIPAL-ID": "operator-1",
    "X-MS-CLIENT-PRINCIPAL-NAME": "operator@example.com",
}


def test_vault_search_filters_by_vertical() -> None:
    mock = VaultApiMock()
    mock.seed_asset(vertical="mobility", function_id="fn-1")
    mock.seed_asset(vertical="finance", function_id="fn-2")
    app.dependency_overrides[get_vault_client] = lambda: mock

    client = TestClient(app)
    response = client.get(
        "/vault-search?object_type=assets&vertical=mobility",
        headers={"Accept": "application/json", **AUTH_HEADERS},
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["rows"]) == 1
    assert body["rows"][0]["vertical"] == "mobility"

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_search_vault_calls_list_assets_with_no_vertical_param() -> None:
    from app.services import search_vault

    fake_client = AsyncMock()
    fake_client.list_assets.return_value = []

    await search_vault(fake_client, object_type="assets", vertical="mobility")

    fake_client.list_assets.assert_called_once_with()
