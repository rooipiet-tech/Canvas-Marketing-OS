"""Console configuration — env-var driven, no hardcoded endpoints.

SCOPE-005: none of these settings is a Postgres connection string/driver
reference — the console never speaks SQL directly. Reads route through the
vault-api contract (mock or real, see clients/) and the Gatekeeper contract
(mock or real), never Postgres.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    vault_api_mode: str  # "mock" | "real"
    vault_api_base_url: str
    gatekeeper_api_mode: str  # "mock" | "real"
    gatekeeper_api_base_url: str
    # F-TEAMS-CARD-REVIEW-LINK (11 Aug 2026): ca-orchestrator's own internal
    # FQDN, for GET /review/{task_id}'s call to GET /tasks/{task_id}/review.
    # No mock mode (see clients/orchestrator_base.py's docstring) -- unset
    # simply means the review page 503s with a clear message, same
    # "config, not code" degrade every other unset base URL here gets.
    orchestrator_api_base_url: str
    applicationinsights_connection_string: str | None
    appinsights_app_id: str | None
    # The workspace-based App Insights resource's underlying Log Analytics
    # workspace id (customerId) — azure-monitor-query's LogsQueryClient
    # queries by workspace id, not by connection string (INFRA-004: this
    # resource is workspace-based, pointed at log-cmos-dev).
    applicationinsights_workspace_id: str | None
    tenant_id: str | None


def get_settings() -> Settings:
    return Settings(
        vault_api_mode=_env("VAULT_API_MODE", "mock"),
        vault_api_base_url=_env("VAULT_API_BASE_URL", "https://vault.internal.cmos.dev"),
        gatekeeper_api_mode=_env("GATEKEEPER_API_MODE", "mock"),
        gatekeeper_api_base_url=_env(
            "GATEKEEPER_API_BASE_URL", "https://gatekeeper.internal.cmos.dev"
        ),
        orchestrator_api_base_url=_env(
            "ORCHESTRATOR_API_BASE_URL", "https://orchestrator.internal.cmos.dev"
        ),
        applicationinsights_connection_string=os.environ.get(
            "APPLICATIONINSIGHTS_CONNECTION_STRING"
        ),
        appinsights_app_id=os.environ.get("APPINSIGHTS_APP_ID"),
        applicationinsights_workspace_id=os.environ.get("APPLICATIONINSIGHTS_WORKSPACE_ID"),
        tenant_id=os.environ.get("TENANT_ID"),
    )
