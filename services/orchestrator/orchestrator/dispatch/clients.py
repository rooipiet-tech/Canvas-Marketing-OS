from __future__ import annotations

import importlib.util
import sys
from typing import Any

from orchestrator.clients.gatekeeper_client import GatekeeperClient, resolve_gatekeeper_base_url
from orchestrator.clients.gateway_client import OrchestratorGatewayClient, resolve_gateway_base_url
from orchestrator.clients.mcp_client import (
    MCPClient,
    resolve_mcp_canva_base_url,
    resolve_mcp_web_base_url,
)
from orchestrator.clients.publisher_client import PublisherClient, resolve_publisher_base_url
from orchestrator.clients.vault_client_ext import VaultClientExt, resolve_vault_base_url
from orchestrator.config import VAULT_API_TOKEN, functions_dir
from orchestrator.dispatch_errors import DispatchError

# ---------------------------------------------------------------------
# Client factories -- separate, monkeypatchable module-level functions
# (not inlined into each handler) so a test can substitute exactly one
# dependency without faking an entire httpx transport chain.
# ---------------------------------------------------------------------

def build_gateway_client() -> OrchestratorGatewayClient:
    return OrchestratorGatewayClient(base_url=resolve_gateway_base_url())

def build_vault_client() -> VaultClientExt:
    return VaultClientExt(base_url=resolve_vault_base_url(), api_token=VAULT_API_TOKEN)

def build_gatekeeper_client() -> GatekeeperClient:
    return GatekeeperClient(base_url=resolve_gatekeeper_base_url())

def build_mcp_web_client() -> MCPClient:
    return MCPClient(base_url=resolve_mcp_web_base_url())

def build_mcp_canva_client() -> MCPClient:
    return MCPClient(base_url=resolve_mcp_canva_base_url())

def build_publisher_client() -> PublisherClient:
    return PublisherClient(base_url=resolve_publisher_base_url())

_PERMISSION_CHECK_MODULE_NAME = "cmos_orchestrator_permission_check"

def load_permission_check() -> Any:
    """Dynamically loads functions/02-brand-steward-qa/permission_check.py
    (L-0039: a digit-prefixed/hyphenated directory name can't be
    dotted-imported) -- reused AS-IS, never forked/duplicated (AC-31's
    "no function logic duplication" requirement)."""
    if _PERMISSION_CHECK_MODULE_NAME in sys.modules:
        return sys.modules[_PERMISSION_CHECK_MODULE_NAME]
    module_path = functions_dir() / "02-brand-steward-qa" / "permission_check.py"
    spec = importlib.util.spec_from_file_location(_PERMISSION_CHECK_MODULE_NAME, module_path)
    if spec is None or spec.loader is None:
        raise DispatchError(f"cannot load permission_check.py from {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_PERMISSION_CHECK_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module

