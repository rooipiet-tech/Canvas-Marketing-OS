"""AC-12, AC-13, AC-24 — the two kill_switch.py copies must not drift.

Gatekeeper and Publisher share no library, so app/kill_switch.py exists
twice on purpose. That duplication is only safe if the two copies behave
identically: a global switch that stops gate decisions but not publishes
(or vice versa) is exactly the failure mode a kill switch exists to
prevent.

Publisher's copy is loaded BY PATH with importlib under a unique module
name — both services ship a top-level `app` package, so a plain import
here would silently resolve to Gatekeeper's own copy and the test would
be comparing a module with itself.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest
from app import kill_switch as gatekeeper_kill_switch

REPO_ROOT = Path(__file__).resolve().parents[3]
PUBLISHER_KILL_SWITCH_PATH = REPO_ROOT / "services" / "publisher" / "app" / "kill_switch.py"


def _load_publisher_kill_switch() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "cmos_publisher_kill_switch_under_test", PUBLISHER_KILL_SWITCH_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def publisher_kill_switch() -> ModuleType:
    return _load_publisher_kill_switch()


def test_both_copies_are_genuinely_distinct_modules(publisher_kill_switch) -> None:
    assert publisher_kill_switch is not gatekeeper_kill_switch
    assert publisher_kill_switch.__file__ != gatekeeper_kill_switch.__file__
    assert Path(publisher_kill_switch.__file__).parts[-3] == "publisher"
    assert Path(gatekeeper_kill_switch.__file__).parts[-3] == "gatekeeper"

    # AC-24: the Postgres-not-Key-Vault rationale is documented in BOTH.
    for module in (gatekeeper_kill_switch, publisher_kill_switch):
        doc = module.__doc__ or ""
        assert "kill switch" in doc.lower()
        assert "Postgres" in doc
        assert "Key Vault" in doc
        assert "governance" in doc
        # And the no-cache guarantee the <5s bound rests on.
        assert "uncached" in doc.lower()
        assert "cache" in doc.lower()


# scope, switch_function_id, queried_function_id, expected_blocked
MATRIX = [
    ("no-switch", None, None, "publish.social_post", False),
    ("no-switch-null-query", None, None, None, False),
    ("global-matches-any-function", "global", None, "publish.social_post", True),
    ("global-matches-other-function", "global", None, "analyse.signal", True),
    ("global-matches-null-function", "global", None, None, True),
    ("function-matches-itself", "function", "publish.social_post", "publish.social_post", True),
    ("function-ignores-other", "function", "publish.social_post", "analyse.signal", False),
    ("function-ignores-null", "function", "publish.social_post", None, False),
]


@pytest.mark.parametrize(
    "label,scope,switch_function_id,queried_function_id,expected_blocked",
    MATRIX,
    ids=[row[0] for row in MATRIX],
)
def test_kill_switch_copies_agree_across_the_full_matrix(
    conn,
    publisher_kill_switch,
    label,
    scope,
    switch_function_id,
    queried_function_id,
    expected_blocked,
) -> None:
    conn.execute("TRUNCATE governance.kill_switches")
    if scope == "global":
        conn.execute(
            "INSERT INTO governance.kill_switches (scope, reason) VALUES ('global', %s)",
            (f"{label} reason",),
        )
    elif scope == "function":
        conn.execute(
            """
            INSERT INTO governance.kill_switches (scope, function_id, reason)
            VALUES ('function', %s, %s)
            """,
            (switch_function_id, f"{label} reason"),
        )

    gatekeeper_status = gatekeeper_kill_switch.is_blocked(conn, queried_function_id)
    publisher_status = publisher_kill_switch.is_blocked(conn, queried_function_id)

    assert gatekeeper_status.blocked is expected_blocked, label
    assert publisher_status.blocked is expected_blocked, label

    # Identical in every field, not merely in the boolean.
    assert gatekeeper_status.blocked == publisher_status.blocked
    assert gatekeeper_status.scope == publisher_status.scope
    assert gatekeeper_status.function_id == publisher_status.function_id
    assert gatekeeper_status.reason == publisher_status.reason
    assert gatekeeper_status.audit_reason == publisher_status.audit_reason

    if expected_blocked:
        assert gatekeeper_status.audit_reason.startswith("kill_switch_active:")
        assert scope in gatekeeper_status.audit_reason
    else:
        assert gatekeeper_status.audit_reason is None


def test_inactive_switches_block_nothing_in_either_copy(conn, publisher_kill_switch) -> None:
    conn.execute("TRUNCATE governance.kill_switches")
    conn.execute(
        "INSERT INTO governance.kill_switches (scope, active, reason) "
        "VALUES ('global', false, 'already cleared')"
    )
    conn.execute(
        "INSERT INTO governance.kill_switches (scope, function_id, active, reason) "
        "VALUES ('function', 'publish.social_post', false, 'already cleared')"
    )

    for function_id in (None, "publish.social_post", "analyse.signal"):
        assert gatekeeper_kill_switch.is_blocked(conn, function_id).blocked is False
        assert publisher_kill_switch.is_blocked(conn, function_id).blocked is False


def test_global_wins_over_function_in_both_copies(conn, publisher_kill_switch) -> None:
    """When both are active, the audit reason must name the global one."""
    conn.execute("TRUNCATE governance.kill_switches")
    conn.execute(
        "INSERT INTO governance.kill_switches (scope, function_id, reason) "
        "VALUES ('function', 'publish.social_post', 'narrow')"
    )
    conn.execute(
        "INSERT INTO governance.kill_switches (scope, reason) VALUES ('global', 'broad')"
    )

    gatekeeper_status = gatekeeper_kill_switch.is_blocked(conn, "publish.social_post")
    publisher_status = publisher_kill_switch.is_blocked(conn, "publish.social_post")

    assert gatekeeper_status.scope == "global"
    assert publisher_status.scope == "global"
    assert gatekeeper_status.audit_reason == publisher_status.audit_reason
