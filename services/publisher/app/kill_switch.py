"""Kill switches — Publisher side (AC-12, AC-13, AC-24).

WHY POSTGRES AND NOT AZURE KEY VAULT (AC-24 rationale)
------------------------------------------------------
The GOAL calls these "Vault-backed flags". In this repository "the Vault"
is Postgres, not Azure Key Vault — contracts/service-bus/spec.md says
"fetch any actual content ... from the Vault (Postgres) by id". The kill
switch therefore lives in the Postgres `governance` schema, deliberately,
for three reasons:

  1. Repo convention: "Vault" == Postgres. Azure Key Vault is always
     named explicitly ("Key Vault") when it is what is meant.
  2. The <5s propagation bound is trivially satisfied by a direct,
     uncached SELECT on the same connection the publish attempt is
     already using. A Key Vault secret read would add a network round
     trip, throttling exposure and a cache-TTL temptation to the hot path
     of every single gate decision and publish attempt.
  3. Kill-switch state is not a secret. It is operational governance
     state that belongs next to publish_attempts so an auditor can join
     "what was blocked" to "why" in one query.

NO CACHING, EVER
----------------
`is_blocked` issues a fresh SELECT on every call. There is no TTL cache,
no memoisation and no module-level state here. Publisher re-checks on
EVERY publish attempt, including attempts carrying a pre-issued,
still-valid gate token — a token issued before the switch was flipped
must not survive it.

This module is deliberately duplicated from services/gatekeeper/app/
kill_switch.py (the two services share no library). The two copies are
kept honest by services/gatekeeper/tests/test_kill_switch_parity.py,
which loads BOTH files and asserts identical behaviour across the full
scope/function_id matrix.
"""

from __future__ import annotations

from dataclasses import dataclass

GLOBAL_SCOPE = "global"
FUNCTION_SCOPE = "function"

KILL_SWITCH_REASON_PREFIX = "kill_switch_active"

_SELECT_ACTIVE_SWITCHES = """
    SELECT scope, function_id, reason
      FROM governance.kill_switches
     WHERE active = true
       AND (
             scope = 'global'
             OR (scope = 'function' AND function_id = %(function_id)s)
           )
     ORDER BY (scope = 'global') DESC, created_at ASC
     LIMIT 1
"""


@dataclass(frozen=True)
class KillSwitchStatus:
    blocked: bool
    scope: str | None = None
    function_id: str | None = None
    reason: str | None = None

    @property
    def audit_reason(self) -> str | None:
        """Reason string recorded on the blocked decision/publish attempt."""
        if not self.blocked:
            return None
        detail = f"{KILL_SWITCH_REASON_PREFIX}:{self.scope}"
        if self.function_id:
            detail = f"{detail}:{self.function_id}"
        if self.reason:
            detail = f"{detail} ({self.reason})"
        return detail


def is_blocked(conn, function_id: str | None = None) -> KillSwitchStatus:
    """Direct, uncached read of the live kill-switch state.

    A global switch blocks regardless of function_id; a function switch
    blocks only its own function_id.
    """
    with conn.cursor() as cur:
        cur.execute(_SELECT_ACTIVE_SWITCHES, {"function_id": function_id})
        row = cur.fetchone()

    if row is None:
        return KillSwitchStatus(blocked=False)

    if isinstance(row, dict):
        scope, matched_function_id, reason = row["scope"], row["function_id"], row["reason"]
    else:
        scope, matched_function_id, reason = row[0], row[1], row[2]

    return KillSwitchStatus(
        blocked=True,
        scope=scope,
        function_id=matched_function_id,
        reason=reason,
    )
