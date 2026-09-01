"""AC-04, AC-23, AC-26 — the Teams Adaptive Card approval affordance."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.approval_inbox import (
    APPROVAL_ROUTE_TEAMS,
    create_approval_request,
    dispatch_approval_request,
)
from app.config import APPROVAL_LINK_TTL_SECONDS

WEBHOOK_URL = "https://prod-00.southafricanorth.logic.azure.com/workflows/fake/triggers/manual"

PREVIEW_TITLE = "Spring campaign teaser post"
PREVIEW_REFERENCE = "asset://campaigns/spring/teaser-v3.png"
EVIDENCE_SUMMARY = "3 of 3 brand checks passed; tone matches brief BR-114; no PII detected."


class _RecordingPost:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, url: str, *, json: dict[str, Any], timeout: float) -> object:
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        return object()


def _make_approval(conn, agent_run):
    return create_approval_request(
        conn,
        gate_decision_id=None,
        agent_run_id=agent_run,
        function_id="publish.social_post",
        action_class="publish",
        level=1,
        content_hash="e" * 64,
        preview_title=PREVIEW_TITLE,
        preview_reference=PREVIEW_REFERENCE,
        evidence_summary=EVIDENCE_SUMMARY,
    )


def _card_content(posted: dict[str, Any]) -> dict[str, Any]:
    return posted["json"]["attachments"][0]["content"]


def _dispatch(conn, agent_run):
    approval = _make_approval(conn, agent_run)
    poster = _RecordingPost()
    route = dispatch_approval_request(
        approval, webhook_url=WEBHOOK_URL, base_url="https://approval.cmos-dev.example",
        http_post=poster,
    )
    assert route == APPROVAL_ROUTE_TEAMS
    assert len(poster.calls) == 1
    return approval, poster.calls[0]


def test_posts_adaptive_card_when_webhook_configured(conn, agent_run) -> None:
    approval, posted = _dispatch(conn, agent_run)

    assert posted["url"] == WEBHOOK_URL
    content = _card_content(posted)

    # Workflows-compatible Adaptive Card (NOT a retired O365 MessageCard).
    assert posted["json"]["attachments"][0]["contentType"] == (
        "application/vnd.microsoft.card.adaptive"
    )
    assert content["type"] == "AdaptiveCard"
    major, minor = (int(part) for part in content["version"].split("."))
    assert (major, minor) >= (1, 4)

    actions = content["actions"]
    assert len(actions) == 2
    titles = [action["title"] for action in actions]
    assert titles == ["Approve", "Reject"]

    link_token = approval["link_token"]
    for action in actions:
        # Links target the authenticated approval-action endpoint, NOT a
        # bare internal API such as /gate-check.
        assert f"/approval-action/{link_token}" in action["url"]
        assert "/gate-check" not in action["url"]
        # Single-use token is URL-safe: no raw-base64 characters.
        assert not set(link_token) & set("+/=")

    assert actions[0]["url"].endswith("?choice=approve")
    assert actions[1]["url"].endswith("?choice=reject")

    # The link expires within 24h of issuance.
    ttl = approval["expires_at"] - datetime.now(timezone.utc)
    assert 0 < ttl.total_seconds() <= APPROVAL_LINK_TTL_SECONDS
    # 30 days since 6 Aug 2026 -- see app/config.py's own note on the
    # trade-off. Still pinned, so widening the window stays a
    # deliberate edit rather than a drift nobody sees.
    assert APPROVAL_LINK_TTL_SECONDS == 30 * 24 * 60 * 60


def test_card_uses_openurl_not_submit_action(conn, agent_run) -> None:
    _approval, posted = _dispatch(conn, agent_run)
    content = _card_content(posted)

    action_types = {action["type"] for action in content["actions"]}
    assert action_types == {"Action.OpenUrl"}
    assert "Action.Submit" not in action_types
    assert "Action.Execute" not in action_types

    # Belt and braces: no submit/execute affordance anywhere in the card.
    serialised = repr(posted["json"])
    assert "Action.Submit" not in serialised
    assert "Action.Execute" not in serialised


def test_card_has_preview_and_evidence_fields(conn, agent_run) -> None:
    _approval, posted = _dispatch(conn, agent_run)
    content = _card_content(posted)

    by_id = {block.get("id"): block for block in content["body"] if block.get("id")}
    assert "preview" in by_id
    assert "evidence-summary" in by_id

    assert PREVIEW_TITLE in by_id["preview"]["text"]
    assert PREVIEW_REFERENCE in by_id["preview"]["text"]
    assert EVIDENCE_SUMMARY in by_id["evidence-summary"]["text"]

    facts = next(block for block in content["body"] if block["type"] == "FactSet")["facts"]
    fact_titles = {fact["title"] for fact in facts}
    assert {"Function", "Action class", "Autonomy level", "Preview reference"} <= fact_titles

    # More than just function_id is present.
    serialised = repr(posted["json"])
    assert serialised.count("publish.social_post") >= 1
    assert PREVIEW_TITLE in serialised and EVIDENCE_SUMMARY in serialised
