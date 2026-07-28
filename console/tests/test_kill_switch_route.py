"""CONSOLE-005: exactly one mutating route (/kill-switch/toggle) exists
across the entire console app; it requires auth."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from app.clients import get_gatekeeper_client
from app.clients.gatekeeper_mock import GatekeeperMock
from app.main import app


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
        headers={
            "X-MS-CLIENT-PRINCIPAL-ID": "operator-1",
            "X-MS-CLIENT-PRINCIPAL-NAME": "operator@example.com",
        },
    )
    assert response.status_code == 200
    assert response.json()["active"] is True

    app.dependency_overrides.clear()
