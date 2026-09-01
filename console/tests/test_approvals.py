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


# Process 6. The inbox showed six columns — function_id, action_class,
# level, preview_title, status, decided_by — all of which describe what
# the POLICY thinks, and none of which describe what is being approved.
# ApprovalRow dropped evidence_summary and content_hash even though the
# Gatekeeper has always stored both. An approver could click; they had
# nothing to disagree with.


def test_evidence_summary_reaches_the_page() -> None:
    """The whole reason the screen exists: a human who cannot see what a
    claim rests on cannot dissent from it."""
    mock = GatekeeperMock()
    mock.seed_approval_inbox(
        status="pending",
        preview_title="Owned-channel newsletter — Fabric-native — fabric-native",
        evidence_summary=(
            "Draft: One number everyone agrees on.\n"
            "Proof points (1), each as cited in the brief:\n"
            "  - Reporting cycles fell from nine days to two "
            "[https://www.moneyweb.co.za/feed/]"
        ),
        content_hash="c" * 64,
    )
    app.dependency_overrides[get_gatekeeper_client] = lambda: mock

    client = TestClient(app)
    response = client.get("/approvals", headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert "Proof points" in response.text
    assert "Reporting cycles fell from nine days to two" in response.text
    assert "moneyweb.co.za" in response.text
    assert "c" * 64 in response.text

    app.dependency_overrides.clear()


def test_a_row_without_evidence_still_renders() -> None:
    """Rows written before the card carried evidence, and any future
    caller that omits it, must not break the queue for every other row."""
    mock = GatekeeperMock()
    mock.seed_approval_inbox(
        status="pending", preview_title="older entry", evidence_summary=""
    )
    app.dependency_overrides[get_gatekeeper_client] = lambda: mock

    client = TestClient(app)
    response = client.get("/approvals", headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert "older entry" in response.text
    assert "Evidence for this approval" not in response.text

    app.dependency_overrides.clear()


def test_the_page_never_renders_a_link_token() -> None:
    """The approve/reject deep link is a single-use credential. The list
    view says what is pending; deciding happens on the separate
    Entra-protected app."""
    mock = GatekeeperMock()
    mock.seed_approval_inbox(
        status="pending",
        preview_title="carousel",
        evidence_summary="Evidence.",
        link_token="super-secret-token-value",
    )
    app.dependency_overrides[get_gatekeeper_client] = lambda: mock

    client = TestClient(app)
    response = client.get("/approvals", headers=AUTH_HEADERS)

    assert "super-secret-token-value" not in response.text

    app.dependency_overrides.clear()


def test_json_view_carries_the_evidence_too() -> None:
    """render_or_json serves the same data to an API consumer; a field
    the HTML shows and the JSON drops would be a second, quieter version
    of the bug this fixes."""
    mock = GatekeeperMock()
    mock.seed_approval_inbox(
        status="pending", preview_title="p", evidence_summary="Proof points (2): ..."
    )
    app.dependency_overrides[get_gatekeeper_client] = lambda: mock

    client = TestClient(app)
    response = client.get(
        "/approvals", headers={**AUTH_HEADERS, "Accept": "application/json"}
    )

    assert response.json()["rows"][0]["evidence_summary"] == "Proof points (2): ..."

    app.dependency_overrides.clear()
