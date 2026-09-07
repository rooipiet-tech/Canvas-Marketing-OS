"""GET/POST /kill-switch, GET /kill-switch/audit/last (TD-10).

console/app/clients/gatekeeper_real.py has called these three routes
since it was written; this proves the routes this session adds actually
satisfy that client's shapes (GatekeeperClient.get_kill_switch_state /
toggle_kill_switch / get_last_audit_entry, console/app/clients/
gatekeeper_base.py), and that toggling here is what app/kill_switch.py's
is_blocked() (already exercised by test_kill_switch_gate_check.py) sees.
"""

from __future__ import annotations


def test_default_state_is_off_with_no_reason(client) -> None:
    response = client.get("/kill-switch")

    assert response.status_code == 200
    assert response.json() == {"active": False, "reason": None, "scope": "global"}


def test_audit_last_is_404_before_any_toggle(client) -> None:
    response = client.get("/kill-switch/audit/last")

    assert response.status_code == 404


def test_toggle_on_then_read_state(client) -> None:
    toggle_response = client.post(
        "/kill-switch/toggle",
        json={"active": True, "reason": "incident 42", "operator": "alice@cmos.example"},
    )
    assert toggle_response.status_code == 200
    assert toggle_response.json() == {
        "active": True,
        "reason": "incident 42",
        "scope": "global",
    }

    state = client.get("/kill-switch").json()
    assert state == {"active": True, "reason": "incident 42", "scope": "global"}


def test_audit_last_reports_operator_after_a_toggle(client) -> None:
    client.post(
        "/kill-switch/toggle",
        json={"active": True, "reason": "incident 42", "operator": "alice@cmos.example"},
    )

    audit = client.get("/kill-switch/audit/last").json()

    assert audit["active"] is True
    assert audit["reason"] == "incident 42"
    assert audit["operator"] == "alice@cmos.example"
    assert audit["decided_at"] is not None
    assert audit["id"] is not None


def test_repeated_toggles_update_the_same_row_not_append(client, conn) -> None:
    """kill_switches models CURRENT state (it has `updated_at`), unlike
    gate_decisions/approval_actions -- a toggle updates in place."""
    client.post(
        "/kill-switch/toggle",
        json={"active": True, "reason": "on", "operator": "alice@cmos.example"},
    )
    client.post(
        "/kill-switch/toggle",
        json={"active": False, "reason": "resolved", "operator": "bob@cmos.example"},
    )
    client.post(
        "/kill-switch/toggle",
        json={"active": True, "reason": "on again", "operator": "carol@cmos.example"},
    )

    count = conn.execute(
        "SELECT count(*) AS n FROM governance.kill_switches WHERE scope = 'global'"
    ).fetchone()["n"]
    assert count == 1

    state = client.get("/kill-switch").json()
    assert state == {"active": True, "reason": "on again", "scope": "global"}

    audit = client.get("/kill-switch/audit/last").json()
    assert audit["operator"] == "carol@cmos.example"


def test_toggle_off_does_not_report_blocked(client, conn) -> None:
    client.post(
        "/kill-switch/toggle",
        json={"active": True, "reason": "on", "operator": "alice@cmos.example"},
    )
    client.post(
        "/kill-switch/toggle",
        json={"active": False, "reason": "resolved", "operator": "bob@cmos.example"},
    )

    state = client.get("/kill-switch").json()
    assert state["active"] is False


def test_toggle_through_the_route_actually_blocks_gate_check(client, conn, agent_run) -> None:
    """The REST toggle must be visible to is_blocked() on the very next
    gate-check -- the same <5s bound test_kill_switch_gate_check.py
    proves for a raw-SQL flip, now proven for the operator-facing path."""
    allowed = client.post(
        "/gate-check",
        json={
            "agent_run_id": str(agent_run),
            "function_id": "analyse.signal",
            "action_class": "analyse",
            "content_hash": "e" * 64,
        },
    ).json()
    assert allowed["outcome"] == "approved"

    client.post(
        "/kill-switch/toggle",
        json={"active": True, "reason": "ops halt", "operator": "alice@cmos.example"},
    )

    blocked = client.post(
        "/gate-check",
        json={
            "agent_run_id": str(agent_run),
            "function_id": "analyse.signal",
            "action_class": "analyse",
            "content_hash": "e" * 64,
        },
    ).json()
    assert blocked["outcome"] == "rejected"
    assert "kill_switch_active:global" in blocked["reason"]


def test_toggle_requires_active_reason_and_operator(client) -> None:
    response = client.post("/kill-switch/toggle", json={"active": True})

    assert response.status_code == 422
