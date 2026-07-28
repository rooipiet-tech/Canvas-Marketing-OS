"""Startup fixture seeding for mock-mode smoke tests.

deploy-console.yml's gated smoke job seeds cost/task/asset/approval-inbox
fixtures into the running console by setting CONSOLE_SEED_FIXTURES_JSON_B64
on the Container App and restarting its revision (see step 23's plan
action). The value is base64-encoded before it becomes a Container Apps
secret/env value — Container Apps silently collapses a literal "$$" to "$"
in secret/env values (the same failure mode this repo already hit and
fixed once for caj-vault-migrate's schema.sql, see git history) — so this
module decodes base64 here rather than accepting raw JSON directly as an
env-var value.
"""

from __future__ import annotations

import base64
import json
import os
from decimal import Decimal
from typing import Any

from app.clients.gatekeeper_mock import GatekeeperMock
from app.clients.vault_api_mock import VaultApiMock

ENV_VAR = "CONSOLE_SEED_FIXTURES_JSON_B64"


def seed_from_env(
    vault_client: Any, gatekeeper_client: Any, *, env: dict[str, str] | None = None
) -> None:
    """No-op unless CONSOLE_SEED_FIXTURES_JSON_B64 is set AND both clients
    are the in-memory mocks (real clients have no seed_* methods)."""
    source = env if env is not None else os.environ
    raw_b64 = source.get(ENV_VAR)
    if not raw_b64:
        return

    decoded = base64.b64decode(raw_b64)
    payload = json.loads(decoded)
    seed_payload(vault_client, gatekeeper_client, payload)


def seed_payload(vault_client: Any, gatekeeper_client: Any, payload: dict[str, Any]) -> None:
    if isinstance(vault_client, VaultApiMock):
        for record in payload.get("agent_runs", []):
            vault_client.seed_agent_run(**record)
        for record in payload.get("assets", []):
            vault_client.seed_asset(**record)
        for record in payload.get("costs", []):
            record = dict(record)
            if "amount" in record:
                record["amount"] = Decimal(str(record["amount"]))
            vault_client.seed_cost(**record)

    if isinstance(gatekeeper_client, GatekeeperMock):
        for record in payload.get("approval_inbox", []):
            gatekeeper_client.seed_approval_inbox(**record)
