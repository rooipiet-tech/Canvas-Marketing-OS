"""Console data-source clients — vault-api and Gatekeeper, mock or real.

SCOPE-005: none of these clients ever speaks SQL/Postgres. Reads route
exclusively through the vault-api contract (mirrors session/s2-vault's
contracts/vault-api.yaml, observed read-only this session) and the
Gatekeeper contract (mirrors session/s4-governance's uncommitted Gatekeeper
API shape, observed read-only this session) — see INTEG-001/INTEG-002 for
the documented mock-to-real cutover.
"""

from __future__ import annotations

from app.clients.app_insights_client import AppInsightsClient
from app.clients.base import VaultApiClient
from app.clients.gatekeeper_base import GatekeeperClient
from app.clients.gatekeeper_mock import GatekeeperMock
from app.clients.gatekeeper_real import GatekeeperHttpClient
from app.clients.orchestrator_base import OrchestratorClient
from app.clients.orchestrator_real import OrchestratorHttpClient
from app.clients.vault_api_mock import VaultApiMock
from app.clients.vault_api_real import VaultApiHttpClient
from app.config import get_settings

# Process-wide singleton mock instances so seeded fixture data (e.g. from a
# test or the deploy-console.yml smoke job's seed step) is visible across
# requests within the same running app instance.
_vault_mock_singleton = VaultApiMock()
_gatekeeper_mock_singleton = GatekeeperMock()


def get_vault_client() -> VaultApiClient:
    settings = get_settings()
    if settings.vault_api_mode == "real":
        return VaultApiHttpClient(base_url=settings.vault_api_base_url)
    return _vault_mock_singleton


def get_gatekeeper_client() -> GatekeeperClient:
    settings = get_settings()
    if settings.gatekeeper_api_mode == "real":
        return GatekeeperHttpClient(base_url=settings.gatekeeper_api_base_url)
    return _gatekeeper_mock_singleton


def get_app_insights_client() -> AppInsightsClient:
    settings = get_settings()
    workspace_id = settings.applicationinsights_workspace_id or ""
    return AppInsightsClient(workspace_id=workspace_id)


def get_orchestrator_client() -> OrchestratorClient:
    settings = get_settings()
    return OrchestratorHttpClient(base_url=settings.orchestrator_api_base_url)


__all__ = [
    "AppInsightsClient",
    "GatekeeperClient",
    "OrchestratorClient",
    "VaultApiClient",
    "get_app_insights_client",
    "get_gatekeeper_client",
    "get_orchestrator_client",
    "get_vault_client",
]
