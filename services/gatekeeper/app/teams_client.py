"""Microsoft Teams approval card (AC-04, AC-23, AC-26).

Classic O365-Connector "Incoming Webhook" endpoints (MessageCard +
potentialAction) were retired in the May 2026 connector shutdown, so
`teams-webhook-url` must be a Workflows (Power Automate) HTTP-trigger URL
and the POST body must be an Adaptive Card, not a MessageCard.

Approve/Reject are Action.OpenUrl DEEP LINKS ONLY. The submit- and
execute-style Adaptive Card action types would require a registered Teams
bot (Bot Framework), which is explicitly out of scope, and - more
importantly - a submit-style postback would make "who clicked" a claim of
the card payload rather than an authenticated identity. The OpenUrl links
point at the Entra-ID-protected approval-action app, where the approver is
taken from the Easy-Auth-injected principal headers (AC-32).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

ADAPTIVE_CARD_CONTENT_TYPE = "application/vnd.microsoft.card.adaptive"
ADAPTIVE_CARD_VERSION = "1.4"
ADAPTIVE_CARD_SCHEMA = "http://adaptivecards.io/schemas/adaptive-card.json"

OPEN_URL_ACTION = "Action.OpenUrl"


def build_adaptive_card(
    *,
    function_id: str,
    action_class: str,
    level: int,
    preview_title: str,
    preview_reference: str | None,
    evidence_summary: str,
    approve_url: str,
    reject_url: str,
    expires_at: datetime,
) -> dict[str, Any]:
    """Build the Workflows-compatible Adaptive Card payload."""
    card_body: list[dict[str, Any]] = [
        {
            "type": "TextBlock",
            "size": "Medium",
            "weight": "Bolder",
            "wrap": True,
            "text": f"Approval required: {preview_title}",
        },
        {
            "type": "FactSet",
            "facts": [
                {"title": "Function", "value": function_id},
                {"title": "Action class", "value": action_class},
                {"title": "Autonomy level", "value": str(level)},
                {"title": "Preview reference", "value": preview_reference or "(none)"},
                {"title": "Link expires", "value": expires_at.isoformat()},
            ],
        },
        {
            "type": "TextBlock",
            "id": "preview",
            "wrap": True,
            "text": f"Preview: {preview_title}"
            + (f" ({preview_reference})" if preview_reference else ""),
        },
        {
            "type": "TextBlock",
            "id": "evidence-summary",
            "wrap": True,
            "text": f"Evidence: {evidence_summary}",
        },
        {
            "type": "TextBlock",
            "isSubtle": True,
            "wrap": True,
            "text": (
                "These links are single-use and expire within 24h. You will be "
                "asked to sign in; the approver recorded is the signed-in "
                "identity, not whoever holds this link."
            ),
        },
    ]

    return {
        "type": "message",
        "attachments": [
            {
                "contentType": ADAPTIVE_CARD_CONTENT_TYPE,
                "contentUrl": None,
                "content": {
                    "$schema": ADAPTIVE_CARD_SCHEMA,
                    "type": "AdaptiveCard",
                    "version": ADAPTIVE_CARD_VERSION,
                    "body": card_body,
                    "actions": [
                        {"type": OPEN_URL_ACTION, "title": "Approve", "url": approve_url},
                        {"type": OPEN_URL_ACTION, "title": "Reject", "url": reject_url},
                    ],
                },
            }
        ],
    }


def post_adaptive_card(
    webhook_url: str,
    card: dict[str, Any],
    *,
    http_post: Callable[..., Any] | None = None,
    timeout: float = 10.0,
) -> Any:
    """POST the card to a Workflows webhook.

    `http_post` is injectable so tests exercise the real payload without a
    network call.
    """
    if http_post is None:
        import httpx

        http_post = httpx.post

    return http_post(webhook_url, json=card, timeout=timeout)
