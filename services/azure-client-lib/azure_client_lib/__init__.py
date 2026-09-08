"""azure-client-lib -- Canvas Marketing OS shared Azure/HTTP-plumbing
primitives.

Extracted (TD-16) from `resolve_live_fqdn`'s three independent copies --
services/orchestrator/orchestrator/clients/azure_fqdn.py,
services/registry/gateway_client.py's resolve_live_gateway_fqdn, and
services/publisher/app/buffer_client.py's own resolve_live_fqdn -- each
shelling out to `az containerapp show` with the identical
query/timeout/failure-handling shape, only one of which (orchestrator's)
carried direct unit test coverage. See fqdn.py's own header for the full
before/after.

Deliberately zero third-party dependencies (stdlib subprocess only) -- see
this package's own pyproject.toml header for why that matters for a
BUNDLE-deployed consumer (Publisher), the same reason governance-lib
(TD-08) is packaged this way rather than the way telemetry-lib is.
"""

from azure_client_lib.fqdn import DEFAULT_RESOURCE_GROUP, resolve_live_fqdn

__all__ = ["DEFAULT_RESOURCE_GROUP", "resolve_live_fqdn"]
