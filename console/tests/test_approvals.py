"""CONSOLE-002: /approvals renders pending+decided entries; no mutating
route exists in routes_reads.py."""

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


def test_approvals_html_renders_both_entries() -> None:
    mock = GatekeeperMock()
    mock.seed_approval_inbox(status="pending", preview_title="pending item")
    mock.seed_approval_inbox(status="approved", preview_title="decided item", decided_by="op-1")
    app.dependency_overrides[get_gatekeeper_client] = lambda: mock

    client = TestClient(app)
    response = client.get("/approvals", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "pending item" in response.text
    assert "decided item" in response.text

    app.dependency_overrides.clear()


def test_routes_reads_has_no_mutating_route() -> None:
    routes_reads_path = Path(__file__).resolve().parents[1] / "app" / "routes_reads.py"
    content = routes_reads_path.read_text(encoding="utf-8")
    assert re.search(r"@app\.(post|put|patch)", content) is None
