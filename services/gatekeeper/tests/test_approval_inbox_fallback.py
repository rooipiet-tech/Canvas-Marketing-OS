"""AC-05, AC-26, AC-27 — local approval-inbox fallback.

This is the PRIMARY end-to-end-tested mode: cmos-dev's Key Vault holds no
teams-webhook-url secret today, so an approval-required decision must
surface through the Gatekeeper-owned inbox table.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.approval_inbox import (
    APPROVAL_ROUTE_INBOX,
    approval_action_url,
    dispatch_approval_request,
)
from app.config import APPROVAL_LINK_TTL_SECONDS

PREVIEW_TITLE = "Autumn launch carousel"
PREVIEW_REFERENCE = "asset://campaigns/autumn/carousel-v1.png"
EVIDENCE_SUMMARY = "Brand check passed; claims sourced from brief BR-220; consent verified."


class _ExplodingPost:
    def __call__(self, *args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("no webhook is configured; nothing may be POSTed")


def _gate_check(client, agent_run):
    response = client.post(
        "/gate-check",
        json={
            "agent_run_id": str(agent_run),
            "function_id": "publish.social_post",
            "action_class": "publish",
            "content_hash": "f" * 64,
            "preview_title": PREVIEW_TITLE,
            "preview_reference": PREVIEW_REFERENCE,
            "evidence_summary": EVIDENCE_SUMMARY,
        },
    )
    assert response.status_code == 200
    return response.json()


def test_falls_back_to_local_inbox_when_webhook_absent(
    client, conn, agent_run, monkeypatch
) -> None:
    monkeypatch.delenv("TEAMS_WEBHOOK_URL", raising=False)

    body = _gate_check(client, agent_run)
    assert body["outcome"] == "escalated"
    assert body["approval_route"] == APPROVAL_ROUTE_INBOX

    rows = conn.execute(
        "SELECT * FROM governance.approval_inbox WHERE agent_run_id = %s", (agent_run,)
    ).fetchall()
    assert len(rows) == 1
    row = rows[0]

    assert row["status"] == "pending"
    assert row["function_id"] == "publish.social_post"
    assert row["content_hash"] == "f" * 64
    assert row["link_token"]
    assert row["link_consumed_at"] is None

    # Single-use link token, URL-safe alphabet only (never raw base64).
    assert not set(row["link_token"]) & set("+/=")

    # Expires within 24h of issuance.
    remaining = row["expires_at"] - datetime.now(timezone.utc)
    assert 0 < remaining.total_seconds() <= APPROVAL_LINK_TTL_SECONDS

    # The surfaced approve/reject links go through the SAME authenticated
    # approval-action endpoint the Teams card would use.
    for url in (body["approve_url"], body["reject_url"]):
        assert f"/approval-action/{row['link_token']}" in url
        assert "/gate-check" not in url
    assert body["approve_url"] == approval_action_url(row["link_token"], "approve")

    # Nothing is ever POSTed anywhere when no webhook is configured.
    assert (
        dispatch_approval_request(dict(row), webhook_url=None, http_post=_ExplodingPost())
        == APPROVAL_ROUTE_INBOX
    )


def test_inbox_row_has_preview_and_evidence_fields(client, conn, agent_run, monkeypatch) -> None:
    monkeypatch.delenv("TEAMS_WEBHOOK_URL", raising=False)
    _gate_check(client, agent_run)

    row = conn.execute(
        "SELECT * FROM governance.approval_inbox WHERE agent_run_id = %s", (agent_run,)
    ).fetchone()

    # A minimum field set beyond function_id: preview reference/title plus
    # an evidence-summary text.
    assert row["preview_title"] == PREVIEW_TITLE
    assert row["preview_reference"] == PREVIEW_REFERENCE
    assert row["evidence_summary"] == EVIDENCE_SUMMARY
    assert row["preview_title"] != row["function_id"]
    assert len(row["evidence_summary"]) > len(row["function_id"])
    assert row["action_class"] == "publish"
    assert row["level"] == 1
