"""VaultApiMock — in-memory, resettable implementation of VaultApiClient.

MOCK — non-authoritative. Mirrors session/s2-vault's uncommitted
contracts/vault-api.yaml shapes (observed read-only this session); ships
only because session/s2-vault has not merged to main yet (see INTEG-001).
No SQL, no CREATE TABLE, no Postgres connection of any kind (SCOPE-005).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


_DEFAULT_TAXONOMY = {
    "vertical": "mobility",
    "function_id": "console-mock-default",
    "campaign": None,
    "evidence_grade": "unverified",
    "consent_status": "not_required",
    "retention_class": "standard_1y",
}


def _with_taxonomy_defaults(fields: dict[str, Any]) -> dict[str, Any]:
    merged = dict(_DEFAULT_TAXONOMY)
    merged.update(fields)
    return merged


class VaultApiMock:
    """In-memory list-backed mock. Resettable per test via `reset()`."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._agent_runs: list[dict[str, Any]] = []
        self._assets: list[dict[str, Any]] = []
        self._opportunity_cards: list[dict[str, Any]] = []
        self._campaigns: list[dict[str, Any]] = []
        self._signals: list[dict[str, Any]] = []
        self._costs: list[dict[str, Any]] = []

    # --- seed helpers -----------------------------------------------------

    def seed_agent_run(self, **fields: Any) -> dict[str, Any]:
        record = _with_taxonomy_defaults(fields)
        record.setdefault("id", _new_id())
        record.setdefault("agent_name", "unnamed-agent")
        record.setdefault("status", "pending")
        record.setdefault("input", {})
        record.setdefault("output", {})
        record.setdefault("started_at", _now().isoformat())
        record.setdefault("completed_at", None)
        record.setdefault("created_at", _now().isoformat())
        self._agent_runs.append(record)
        return record

    def seed_asset(self, **fields: Any) -> dict[str, Any]:
        record = _with_taxonomy_defaults(fields)
        record.setdefault("id", _new_id())
        record.setdefault("asset_type", "social_post_image")
        record.setdefault("version", 1)
        record.setdefault("approval_state", "draft")
        record.setdefault("agent_run_id", _new_id())
        record.setdefault("content_hash", "deadbeef")
        record.setdefault("storage_uri", None)
        record.setdefault("created_at", _now().isoformat())
        self._assets.append(record)
        return record

    def seed_opportunity_card(self, **fields: Any) -> dict[str, Any]:
        record = _with_taxonomy_defaults(fields)
        record.setdefault("id", _new_id())
        record.setdefault("title", "untitled opportunity")
        record.setdefault("status", "new")
        record.setdefault("score", None)
        record.setdefault("created_at", _now().isoformat())
        self._opportunity_cards.append(record)
        return record

    def seed_campaign(self, **fields: Any) -> dict[str, Any]:
        record = _with_taxonomy_defaults(fields)
        record.setdefault("id", _new_id())
        record.setdefault("name", "unnamed campaign")
        record.setdefault("status", "draft")
        record.setdefault("created_at", _now().isoformat())
        self._campaigns.append(record)
        return record

    def seed_signal(self, **fields: Any) -> dict[str, Any]:
        record = _with_taxonomy_defaults(fields)
        record.setdefault("id", _new_id())
        record.setdefault("source", "unknown")
        record.setdefault("signal_type", "unknown")
        record.setdefault("payload", {})
        record.setdefault("received_at", _now().isoformat())
        record.setdefault("created_at", _now().isoformat())
        self._signals.append(record)
        return record

    def seed_cost(self, **fields: Any) -> dict[str, Any]:
        record = _with_taxonomy_defaults(fields)
        record.setdefault("id", _new_id())
        record.setdefault("agent_run_id", _new_id())
        record.setdefault("provider", "anthropic")
        record.setdefault("unit", "usd")
        amount = record.get("amount", Decimal("0"))
        record["amount"] = amount if isinstance(amount, Decimal) else Decimal(str(amount))
        record.setdefault("incurred_at", _now().isoformat())
        record.setdefault("created_at", _now().isoformat())
        self._costs.append(record)
        return record

    # --- VaultApiClient protocol methods -----------------------------------

    async def list_agent_runs(
        self, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
        return self._agent_runs[offset : offset + limit]

    async def list_assets(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        return self._assets[offset : offset + limit]

    async def list_opportunity_cards(
        self, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
        return self._opportunity_cards[offset : offset + limit]

    async def list_campaigns(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        return self._campaigns[offset : offset + limit]

    async def list_signals(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        return self._signals[offset : offset + limit]

    async def list_costs(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        return self._costs[offset : offset + limit]
