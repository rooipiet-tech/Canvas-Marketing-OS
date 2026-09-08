"""Pure-unit coverage for governance_lib.kill_switch — no real Postgres.

The real scope/function_id matrix against a live `governance.kill_switches`
table is exercised end-to-end by
services/gatekeeper/tests/test_kill_switch_parity.py (which now imports
this exact module from both Gatekeeper's and Publisher's thin per-service
wrappers). This suite only proves is_blocked's own row-shape handling and
KillSwitchStatus.audit_reason, using a fake connection so it needs no
database at all.
"""

from __future__ import annotations

from governance_lib.kill_switch import (
    FUNCTION_SCOPE,
    GLOBAL_SCOPE,
    KILL_SWITCH_REASON_PREFIX,
    KillSwitchStatus,
    is_blocked,
)


class _FakeCursor:
    def __init__(self, row) -> None:
        self._row = row
        self.executed_with: dict | None = None

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *exc_info) -> None:
        return None

    def execute(self, _query: str, params: dict) -> None:
        self.executed_with = params

    def fetchone(self):
        return self._row


class _FakeConn:
    def __init__(self, row) -> None:
        self._row = row

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self._row)


def test_no_active_switch_is_not_blocked() -> None:
    status = is_blocked(_FakeConn(None), "publish.social_post")
    assert status == KillSwitchStatus(blocked=False)
    assert status.audit_reason is None


def test_tuple_row_is_blocked_with_audit_reason() -> None:
    status = is_blocked(_FakeConn((GLOBAL_SCOPE, None, "incident")), None)
    assert status.blocked is True
    assert status.scope == GLOBAL_SCOPE
    assert status.function_id is None
    assert status.audit_reason == f"{KILL_SWITCH_REASON_PREFIX}:global (incident)"


def test_dict_row_is_blocked_with_function_scope() -> None:
    row = {"scope": FUNCTION_SCOPE, "function_id": "publish.social_post", "reason": None}
    status = is_blocked(_FakeConn(row), "publish.social_post")
    assert status.blocked is True
    assert status.scope == FUNCTION_SCOPE
    assert status.function_id == "publish.social_post"
    assert status.audit_reason == f"{KILL_SWITCH_REASON_PREFIX}:function:publish.social_post"


def test_audit_reason_is_none_unless_blocked() -> None:
    assert KillSwitchStatus(blocked=False, scope=GLOBAL_SCOPE).audit_reason is None
