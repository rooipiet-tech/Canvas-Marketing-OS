"""VaultApiClient Protocol — mirrors session/s2-vault's contracts/vault-api.yaml
list-endpoint shapes (observed read-only this session at
C:\\Users\\rooip\\cmos-s2\\contracts\\vault-api.yaml, uncommitted on
session/s2-vault).

Every object returned by any list_* method carries the 6 mandatory
taxonomy fields — vertical, function_id, campaign, evidence_grade,
consent_status, retention_class — as first-class properties, per the real
contract's TaxonomyFields component.

As observed, the real vault-api contract's list endpoints support only
Limit/Offset pagination — no server-side taxonomy filter parameter (see
CONSOLE-003's "interim mechanism" note). This Protocol therefore exposes
only limit/offset on every list_* method; any taxonomy filtering happens
client-side in console/app/services.py.

INTEG-001: VaultApiMock and VaultApiHttpClient both implement this exact
Protocol, so switching VAULT_API_MODE from "mock" to "real" is a pure
config change — no code change to any route/service that depends on this
Protocol.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Protocol


class VaultApiClient(Protocol):
    async def list_agent_runs(
        self, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]: ...

    async def list_assets(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]: ...

    async def list_opportunity_cards(
        self, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]: ...

    async def list_campaigns(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]: ...

    async def list_signals(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]: ...

    async def list_costs(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]: ...


# The Cost schema's `amount` is typed and stored as decimal.Decimal end to
# end (never float, never int-as-cents) — see console/app/clients/
# vault_api_mock.py and vault_api_real.py, and console/app/rendering.py's
# decimal_json_encoder — establishing the single canonical numeric
# representation GOAL-002's byte-for-byte cost comparison depends on.
CostAmount = Decimal
