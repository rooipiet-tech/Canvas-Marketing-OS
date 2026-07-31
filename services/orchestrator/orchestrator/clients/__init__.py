"""orchestrator.clients — thin HTTP clients the 5 new dispatch handlers
(orchestrator/dispatch.py) use to make REAL calls to model-gateway,
Vault, Gatekeeper and mcp-web (AC-01's "real downstream effects").

Every client resolves its target service's address LIVE (via
`az containerapp show`, see clients/azure_fqdn.py) or from an explicit
env-var override — never a hardcoded FQDN (AC-19, L-0025). Every
gateway-bound request references only logical model tiers
(haiku/sonnet/opus), never a physical model id (AC-20, L-0026).
"""

from __future__ import annotations

from orchestrator.clients.gatekeeper_client import GatekeeperClient
from orchestrator.clients.gateway_client import LOGICAL_TIERS, OrchestratorGatewayClient
from orchestrator.clients.mcp_client import MCPClient
from orchestrator.clients.vault_client_ext import VaultClientExt

__all__ = [
    "GatekeeperClient",
    "LOGICAL_TIERS",
    "OrchestratorGatewayClient",
    "MCPClient",
    "VaultClientExt",
]
