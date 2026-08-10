"""Teams posting of the morning brief (plan step 9; AC-25).

Mirrors services/gatekeeper/app/teams_client.py's Workflows-webhook +
Adaptive Card + Action.OpenUrl pattern (L-0033: classic O365-Connector
incoming webhooks are retired) -- a SEPARATE, independent implementation
(no cross-service import; the two services share no library) but the
same wire shape.

teams-webhook-url does NOT exist in Key Vault yet (LIVE PLATFORM FACTS),
so notify_brief_ready() no-ops by default: the brief still 'lands'
observably via the Vault write draft_brief_handler already made (visible
through console's approval-inbox-equivalent surface) regardless of
whether this call does anything. Activates the instant a real
TEAMS_WEBHOOK_URL env var appears -- a config flag flip, never a code
change (per the GOAL's own framing of this as a named human follow-up
task).
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable

from orchestrator.logging_config import get_logger, log_event

logger = get_logger("teams_notify")

ADAPTIVE_CARD_CONTENT_TYPE = "application/vnd.microsoft.card.adaptive"
ADAPTIVE_CARD_VERSION = "1.4"
ADAPTIVE_CARD_SCHEMA = "http://adaptivecards.io/schemas/adaptive-card.json"


def teams_webhook_url() -> str | None:
    return os.environ.get("TEAMS_WEBHOOK_URL") or None


def build_brief_card(*, title: str, brief_id: str, executive_brief_id: str) -> dict[str, Any]:
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
                    "body": [
                        {
                            "type": "TextBlock",
                            "size": "Medium",
                            "weight": "Bolder",
                            "wrap": True,
                            "text": f"Morning brief ready: {title}",
                        },
                        {
                            "type": "FactSet",
                            "facts": [
                                {"title": "Brief id", "value": brief_id},
                                {"title": "Executive edition id", "value": executive_brief_id},
                            ],
                        },
                    ],
                },
            }
        ],
    }


def notify_brief_ready(
    *,
    title: str,
    brief_id: str,
    executive_brief_id: str,
    webhook_url: str | None = None,
    http_post: Callable[..., Any] | None = None,
) -> bool:
    """Posts the brief-ready Adaptive Card if (and only if) a Teams
    webhook is configured. Returns True if a POST was attempted, False if
    this no-op'd (AC-25's "zero Teams POSTs when unset" assertion)."""
    resolved = webhook_url if webhook_url is not None else teams_webhook_url()
    if not resolved:
        log_event(
            logger, logging.INFO, "teams_notify_skipped_no_webhook", brief_id=brief_id
        )
        return False

    if http_post is None:
        import httpx

        http_post = httpx.post

    card = build_brief_card(title=title, brief_id=brief_id, executive_brief_id=executive_brief_id)
    try:
        http_post(resolved, json=card, timeout=10.0)
    except Exception as exc:  # noqa: BLE001 - a Teams-posting failure must never break the loop
        log_event(logger, logging.WARNING, "teams_notify_post_failed", error=str(exc))
    return True


def build_needs_edit_card(
    *, task_id: str, channel: str, violations: list[str], draft_excerpt: str
) -> dict[str, Any]:
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
                    "body": [
                        {
                            "type": "TextBlock",
                            "size": "Medium",
                            "weight": "Bolder",
                            "wrap": True,
                            "text": "Needs edit: Brand Steward QA blocked a draft",
                        },
                        {
                            "type": "FactSet",
                            "facts": [
                                {"title": "Task id", "value": task_id},
                                {"title": "Channel", "value": channel},
                                {"title": "Violations", "value": ", ".join(violations) or "unknown"},
                            ],
                        },
                        {"type": "TextBlock", "wrap": True, "isSubtle": True, "text": draft_excerpt},
                    ],
                },
            }
        ],
    }


def notify_needs_edit(
    *,
    task_id: str,
    channel: str,
    violations: list[str],
    draft_excerpt: str,
    webhook_url: str | None = None,
    http_post: Callable[..., Any] | None = None,
) -> bool:
    """Posts a 'needs edit' Adaptive Card when Brand Steward QA blocks a
    draft (Proposal C, qa-feedback-loop-proposal-2026-08-05.md). Same
    AC-25 flag-gate pattern as notify_brief_ready: no-ops with zero POSTs
    when TEAMS_WEBHOOK_URL is unset. Never raises -- a notification
    failure must never affect QA-gate correctness."""
    resolved = webhook_url if webhook_url is not None else teams_webhook_url()
    if not resolved:
        log_event(
            logger, logging.INFO, "teams_notify_needs_edit_skipped_no_webhook", task_id=task_id
        )
        return False

    if http_post is None:
        import httpx

        http_post = httpx.post

    card = build_needs_edit_card(
        task_id=task_id, channel=channel, violations=violations, draft_excerpt=draft_excerpt
    )
    try:
        http_post(resolved, json=card, timeout=10.0)
    except Exception as exc:  # noqa: BLE001 - a Teams-posting failure must never break the loop
        log_event(logger, logging.WARNING, "teams_notify_needs_edit_post_failed", error=str(exc))
    return True
