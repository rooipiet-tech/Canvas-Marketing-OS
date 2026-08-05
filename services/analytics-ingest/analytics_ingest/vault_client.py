"""analytics_ingest.vault_client — Vault REST client.

Reads the Vault EXCLUSIVELY via its HTTP API (httpx), never a direct
database-driver connection to the Vault's own database (C-VAULT-TABLES /
AC-06). VAULT_API_URL is read from os.environ only — no hardcoded
Vault FQDN literal anywhere in this module (AC-30).

Every GET carries an X-Caller-Service: analytics-ingest header, per
contracts/vault-api.yaml's CallerService parameter — the Vault service
records this for its own utilisation rollup.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

CALLER_SERVICE = "analytics-ingest"


def _base_url() -> str:
    return os.environ["VAULT_API_URL"]


def _headers() -> dict[str, str]:
    return {"X-Caller-Service": CALLER_SERVICE}


async def _get_paginated(path: str, limit: int) -> list[dict[str, Any]]:
    """GET `path`, looping on the `offset` query param (contracts/vault-api.yaml's
    Limit/Offset parameters, both defined on every list endpoint below)
    until a page shorter than `limit` (or empty) comes back, concatenating
    every page. Without this loop, a Vault table that ever exceeds `limit`
    rows would be silently truncated — not an error, just quietly wrong
    downstream rollup values.
    """
    results: list[dict[str, Any]] = []
    offset = 0
    async with httpx.AsyncClient(base_url=_base_url()) as client:
        while True:
            resp = await client.get(
                path, params={"limit": limit, "offset": offset}, headers=_headers()
            )
            resp.raise_for_status()
            page = resp.json()
            results.extend(page)
            if len(page) < limit:
                break
            offset += limit
    return results


async def get_costs(limit: int = 500) -> list[dict[str, Any]]:
    """GET /costs — every cost entry, paginated internally via offset."""
    return await _get_paginated("/costs", limit)


async def get_agent_runs(limit: int = 500) -> list[dict[str, Any]]:
    """GET /agent-runs — every agent run, paginated internally via offset."""
    return await _get_paginated("/agent-runs", limit)


async def get_assets(limit: int = 500) -> list[dict[str, Any]]:
    """GET /assets — every asset summary, paginated internally via offset."""
    return await _get_paginated("/assets", limit)


async def get_campaigns(limit: int = 500) -> list[dict[str, Any]]:
    """GET /campaigns — every campaign, paginated internally via offset."""
    return await _get_paginated("/campaigns", limit)


async def get_utilisation_rollup(date_from: str, date_to: str) -> list[dict[str, Any]]:
    """GET /utilisation/rollup?from=...&to=... — Vault's own daily rollup.

    Sources the vault-utilisation KPI (AC-22) from Vault's own precomputed
    rollup, never reinvented from raw access logs.
    """
    async with httpx.AsyncClient(base_url=_base_url()) as client:
        resp = await client.get(
            "/utilisation/rollup",
            params={"from": date_from, "to": date_to},
            headers=_headers(),
        )
        resp.raise_for_status()
        return resp.json()
