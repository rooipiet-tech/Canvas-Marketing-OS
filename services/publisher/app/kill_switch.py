"""Kill switches — Publisher side (AC-12, AC-13, AC-24).

TD-08: this module used to be a hand-duplicated copy of
services/gatekeeper/app/kill_switch.py (the two services shared no
library), kept honest by
services/gatekeeper/tests/test_kill_switch_parity.py, which loads BOTH
files by path and asserts identical behaviour across the full
scope/function_id matrix. It now re-exports the single implementation in
services/governance-lib/governance_lib/kill_switch.py, which carries the
full rationale: the kill switch lives in Postgres, not Azure Key Vault,
because a Key Vault secret read would add a network round trip and a
cache-TTL temptation to the hot path of every gate decision and publish
attempt, whereas an uncached SELECT on the connection already in use
satisfies the <5s propagation bound directly — no memoisation, no
module-level cache, no state here at all. Publisher re-checks on EVERY
publish attempt, including one carrying a pre-issued, still-valid gate
token — a token issued before the switch was flipped must not survive it.

Publisher is a BUNDLE-deployed service (no Dockerfile, no sibling-package
install mechanism — see app/telemetry_wiring.py's INCIDENT note), so
governance_lib is embedded here as plain source via BUNDLE_MANIFEST.txt +
infra/main.bicep's loadTextContent, not pip-installed at runtime. This
file still exists, separately from Gatekeeper's own copy, only because
each service's bundle needs its own `app/kill_switch.py` at this exact
import path — both now resolve to the SAME governance_lib source, so
test_kill_switch_parity.py's cross-file comparison is trivially true
rather than a live drift detector.
"""

from __future__ import annotations

from governance_lib.kill_switch import (
    FUNCTION_SCOPE,
    GLOBAL_SCOPE,
    KILL_SWITCH_REASON_PREFIX,
    KillSwitchStatus,
    is_blocked,
)

__all__ = [
    "FUNCTION_SCOPE",
    "GLOBAL_SCOPE",
    "KILL_SWITCH_REASON_PREFIX",
    "KillSwitchStatus",
    "is_blocked",
]
