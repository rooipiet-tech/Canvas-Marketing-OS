"""governance-lib — Canvas Marketing OS shared governance primitives.

Extracted (TD-08) from three behaviours previously hand-duplicated across
Gatekeeper and Publisher (and, for AGENT_NAME_LOOP_PROOF, the
orchestrator), each kept honest only by a cross-service parity test:

  * kill_switch — the Postgres-backed kill switch (AC-12, AC-13, AC-24).
  * resource_claim — the gate-token `resource` claim's canonical-JSON
    separators and parse/validate logic (AC-06, AC-08).
  * constants.AGENT_NAME_LOOP_PROOF — the S8 proof circuit's synthetic
    agent_run label.

See this package's own pyproject.toml header for why it carries zero
third-party dependencies (it must be embeddable as plain source into
Gatekeeper's and Publisher's BUNDLE-deployed source bundles, not only
pip-installable the way telemetry-lib is for Docker-built services).
"""

from governance_lib.constants import AGENT_NAME_LOOP_PROOF
from governance_lib.kill_switch import (
    FUNCTION_SCOPE,
    GLOBAL_SCOPE,
    KILL_SWITCH_REASON_PREFIX,
    KillSwitchStatus,
    is_blocked,
)
from governance_lib.resource_claim import (
    CANONICAL_JSON_SEPARATORS,
    REQUIRED_RESOURCE_CLAIM_KEYS,
    canonicalize_resource_claim,
    parse_resource_claim,
)

__all__ = [
    "AGENT_NAME_LOOP_PROOF",
    "CANONICAL_JSON_SEPARATORS",
    "FUNCTION_SCOPE",
    "GLOBAL_SCOPE",
    "KILL_SWITCH_REASON_PREFIX",
    "REQUIRED_RESOURCE_CLAIM_KEYS",
    "KillSwitchStatus",
    "canonicalize_resource_claim",
    "is_blocked",
    "parse_resource_claim",
]
