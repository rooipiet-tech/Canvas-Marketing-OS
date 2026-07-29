"""CONSOLE-005: exactly one mutating route (/kill-switch/toggle) exists
across the entire console app; it requires auth."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from app.clients import get_gatekeeper_client
from app.clients.gatekeeper_mock import GatekeeperMock
from app.main import app

AUTH_HEADERS = {
    "X-MS-CLIENT-PRINCIPAL-ID": "operator-1",
    "X-MS-CLIENT-PRINCIPAL-NAME": "operator@example.com",
}

# RE-4: the form-encoded (browser) path now requires a matching
# same-origin Origin/Referer header (CSRF defense-in-depth) — TestClient's
# default base_url is http://testserver, so a real same-origin browser
# submission is simulated with this Origin value.
SAME_ORIGIN_HEADERS = {**AUTH_HEADERS, "Origin": "http://testserver"}


def test_exactly_one_mutating_route_in_whole_console() -> None:
    app_dir = Path(__file__).resolve().parents[1] / "app"
    pattern = re.compile(r'@app\.(post|put|patch|delete)\("([^"]+)"\)')
    matches: set[str] = set()
    for py_file in app_dir.glob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        for _, path in pattern.findall(content):
            matches.add(path)
    assert matches == {"/kill-switch/toggle"}


def test_unauthenticated_post_returns_401() -> None:
    mock = GatekeeperMock()
    app.dependency_overrides[get_gatekeeper_client] = lambda: mock
    client = TestClient(app)

    response = client.post("/kill-switch/toggle", json={"active": True, "reason": "test"})
    assert response.status_code == 401

    app.dependency_overrides.clear()


def test_authenticated_post_toggles_and_returns_state() -> None:
    mock = GatekeeperMock()
    app.dependency_overrides[get_gatekeeper_client] = lambda: mock
    client = TestClient(app)

    response = client.post(
        "/kill-switch/toggle",
        json={"active": True, "reason": "test"},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200
    assert response.json()["active"] is True

    app.dependency_overrides.clear()


def test_form_encoded_post_toggles_and_redirects_to_kill_switch_page() -> None:
    """Regression test for the bug that shipped in build v1: the real
    <form> in kill_switch.html has no `enctype`, so a real browser submits
    `application/x-www-form-urlencoded` (`reason=<text>&active=true`), not
    JSON. Replicates that exact shape via TestClient's `data=` kwarg (which
    sets Content-Type: application/x-www-form-urlencoded, matching a real
    browser's default form submission — no JS involved).

    POLISH-006: a real (no-JS) browser submission now gets a 303
    POST-redirect-GET back to the styled /kill-switch screen instead of a
    bare JSON response — verified here by disabling redirect-following and
    checking the redirect itself, then confirming the toggle actually took
    effect via a separate GET."""
    mock = GatekeeperMock()
    app.dependency_overrides[get_gatekeeper_client] = lambda: mock
    client = TestClient(app, follow_redirects=False)

    response = client.post(
        "/kill-switch/toggle",
        data={"reason": "form-submitted reason", "active": "true"},
        headers=SAME_ORIGIN_HEADERS,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/kill-switch"

    state_response = client.get(
        "/kill-switch", headers={"Accept": "application/json", **AUTH_HEADERS}
    )
    assert state_response.status_code == 200
    state = state_response.json()["state"]
    assert state["active"] is True
    assert state["reason"] == "form-submitted reason"

    app.dependency_overrides.clear()


def test_form_encoded_post_deactivate_parses_false() -> None:
    """The form's second submit button posts active=false — confirm the
    string 'false' is parsed as a real Python False, not truthy-by-non-empty."""
    mock = GatekeeperMock()
    app.dependency_overrides[get_gatekeeper_client] = lambda: mock
    client = TestClient(app, follow_redirects=False)

    response = client.post(
        "/kill-switch/toggle",
        data={"reason": "deactivating", "active": "false"},
        headers=SAME_ORIGIN_HEADERS,
    )
    assert response.status_code == 303

    state_response = client.get(
        "/kill-switch", headers={"Accept": "application/json", **AUTH_HEADERS}
    )
    assert state_response.json()["state"]["active"] is False

    app.dependency_overrides.clear()


def test_json_post_still_returns_json_body_directly() -> None:
    """AGENT-002's programmatic-client contract (application/json in,
    application/json out, no redirect) must be unaffected by POLISH-006's
    form-only redirect."""
    mock = GatekeeperMock()
    app.dependency_overrides[get_gatekeeper_client] = lambda: mock
    client = TestClient(app, follow_redirects=False)

    response = client.post(
        "/kill-switch/toggle",
        json={"active": True, "reason": "json client"},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["active"] is True
    assert body["reason"] == "json client"

    app.dependency_overrides.clear()


def test_form_encoded_unauthenticated_post_returns_401() -> None:
    mock = GatekeeperMock()
    app.dependency_overrides[get_gatekeeper_client] = lambda: mock
    client = TestClient(app)

    response = client.post(
        "/kill-switch/toggle", data={"reason": "test", "active": "true"}
    )
    assert response.status_code == 401

    app.dependency_overrides.clear()


def test_form_encoded_cross_origin_post_rejected() -> None:
    """RE-4: a plain HTML form has no CSRF token, so a malicious
    third-party page could otherwise trigger this exact cross-origin
    submission with the victim's Easy-Auth session cookie forwarded
    automatically. An authenticated-but-cross-origin submission must be
    rejected (403), and a submission with no Origin/Referer at all
    (the same shape a script-triggered cross-site form POST produces)
    must also be rejected, fail-closed."""
    mock = GatekeeperMock()
    app.dependency_overrides[get_gatekeeper_client] = lambda: mock
    client = TestClient(app, follow_redirects=False)

    cross_origin_response = client.post(
        "/kill-switch/toggle",
        data={"reason": "attacker", "active": "true"},
        headers={**AUTH_HEADERS, "Origin": "https://evil.example.com"},
    )
    assert cross_origin_response.status_code == 403

    no_origin_response = client.post(
        "/kill-switch/toggle",
        data={"reason": "attacker", "active": "true"},
        headers=AUTH_HEADERS,
    )
    assert no_origin_response.status_code == 403

    # The JSON API path (AGENT-002) is untouched by this check — a
    # cross-origin script cannot set Content-Type: application/json
    # against this endpoint without triggering a CORS preflight this app
    # never opts into, so no same-origin check is needed there.
    json_response = client.post(
        "/kill-switch/toggle",
        json={"active": True, "reason": "legit agent"},
        headers={**AUTH_HEADERS, "Origin": "https://evil.example.com"},
    )
    assert json_response.status_code == 200

    app.dependency_overrides.clear()


def test_get_kill_switch_exposes_last_audit_entry_with_operator_identity() -> None:
    """GOAL-004: an HTTP client must be able to GET the audit entry for a
    toggle and assert the operator-identity field equals the operator that
    performed it — previously only reachable at the Python object level
    via GatekeeperMock.get_last_audit_entry()."""
    mock = GatekeeperMock()
    app.dependency_overrides[get_gatekeeper_client] = lambda: mock
    client = TestClient(app)

    toggle_response = client.post(
        "/kill-switch/toggle",
        json={"active": True, "reason": "incident"},
        headers=AUTH_HEADERS,
    )
    assert toggle_response.status_code == 200

    state_response = client.get(
        "/kill-switch", headers={"Accept": "application/json", **AUTH_HEADERS}
    )
    assert state_response.status_code == 200
    body = state_response.json()
    last_audit_entry = body["state"]["last_audit_entry"]
    assert last_audit_entry is not None
    assert last_audit_entry["operator"] == "operator@example.com (operator-1)"
    assert last_audit_entry["active"] is True
    assert last_audit_entry["reason"] == "incident"

    app.dependency_overrides.clear()


def test_get_kill_switch_last_audit_entry_is_none_before_any_toggle() -> None:
    mock = GatekeeperMock()
    app.dependency_overrides[get_gatekeeper_client] = lambda: mock
    client = TestClient(app)

    response = client.get("/kill-switch", headers={"Accept": "application/json", **AUTH_HEADERS})
    assert response.status_code == 200
    assert response.json()["state"]["last_audit_entry"] is None

    app.dependency_overrides.clear()
