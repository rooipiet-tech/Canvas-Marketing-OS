"""VaultApiHttpClient — genuine httpx implementation of VaultApiClient.

Talks to the real vault-api Container App (session/s2-vault's
contracts/vault-api.yaml) over its internal ingress. This class exists so
INTEG-001's cutover is truly config-only: switching VAULT_API_MODE from
"mock" to "real" (and setting VAULT_API_BASE_URL to the real Container
App's FQDN) requires no code change anywhere else in the console — every
caller depends only on the VaultApiClient Protocol.

Cost `amount` is parsed to decimal.Decimal from the JSON response (JSON
numbers have no native Decimal type), preserving the same canonical
numeric representation VaultApiMock uses internally.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import httpx


class VaultApiHttpClient:
    def __init__(self, base_url: str, *, timeout: float = 10.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    async def _list(self, path: str, limit: int, offset: int) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout) as client:
            response = await client.get(path, params={"limit": limit, "offset": offset})
            response.raise_for_status()
            return response.json()

    async def list_agent_runs(
        self, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
        return await self._list("/agent-runs", limit, offset)

    async def list_assets(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        return await self._list("/assets", limit, offset)

    async def list_opportunity_cards(
        self, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
        return await self._list("/opportunity-cards", limit, offset)

    async def list_campaigns(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        return await self._list("/campaigns", limit, offset)

    async def list_signals(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        return await self._list("/signals", limit, offset)

    async def list_costs(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        rows = await self._list("/costs", limit, offset)
        for row in rows:
            if "amount" in row and not isinstance(row["amount"], Decimal):
                row["amount"] = Decimal(str(row["amount"]))
        return rows
