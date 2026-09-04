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

F-TEAMS-CARD-REVIEW-LINK (11 Aug 2026): the docstring above always claimed
this mirrors gatekeeper's Action.OpenUrl pattern, but build_needs_edit_card
and build_retry_exhausted_card never actually carried an "actions" block --
confirmed live the moment TEAMS_WEBHOOK_URL went live tonight (F-TEAMS-
NEEDS-EDIT-WEBHOOK): Pieter saw real cards in Teams with a task id and no
way to act on it. Fixed by adding an optional "Review in console"
Action.OpenUrl button to both "needs edit" cards, pointed at the new
GET /tasks/{task_id}/review orchestrator endpoint via console's new
GET /review/{task_id} page. Gated on CMOS_CONSOLE_BASE_URL exactly like
TEAMS_WEBHOOK_URL gates the POST itself -- unset means no "actions" block
at all (today's shape, zero behavior change) rather than a dead link.
build_brief_card intentionally does NOT get a button yet: there is no
per-brief review page (only per-task_id), and the brief is FYI-only, not
an action queue item -- left as a follow-up, not silently done here.
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
OPEN_URL_ACTION = "Action.OpenUrl"


def teams_webhook_url() -> str | None:
    return os.environ.get("TEAMS_WEBHOOK_URL") or None


def console_base_url() -> str | None:
    """Base URL of the operator console's GET /review/{task_id} page --
    see F-TEAMS-CARD-REVIEW-LINK above. Unset (LIVE PLATFORM FACTS, until
    the matching infra param lands) means no review link, not a broken one."""
    return os.environ.get("CMOS_CONSOLE_BASE_URL") or None


def _review_url(base_url: str, task_id: str) -> str:
    return f"{base_url.rstrip('/')}/review/{task_id}"


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
    *,
    task_id: str,
    channel: str,
    violations: list[str],
    draft_excerpt: str,
    review_url: str | None = None,
) -> dict[str, Any]:
    content: dict[str, Any] = {
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
                    {
                        "title": "Violations",
                        "value": ", ".join(violations) or "unknown",
                    },
                ],
            },
            {
                "type": "TextBlock",
                "wrap": True,
                "isSubtle": True,
                "text": draft_excerpt,
            },
        ],
    }
    if review_url:
        content["actions"] = [
            {"type": OPEN_URL_ACTION, "title": "Review in console", "url": review_url}
        ]
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": ADAPTIVE_CARD_CONTENT_TYPE,
                "contentUrl": None,
                "content": content,
            }
        ],
    }


def build_retry_exhausted_card(
    *,
    task_id: str,
    draft_task_id: str,
    channel: str,
    violations: list[str],
    original_excerpt: str,
    revised_excerpt: str,
    diff_text: str,
    attempts: int,
    hollowed: bool,
    review_url: str | None = None,
) -> dict[str, Any]:
    """F-QA-RETRY-LOOP (11 Aug 2026, reopened qa-feedback-loop-proposal-
    2026-08-05.md): Phase 2 card -- posted once the bounded auto-retry
    loop (dispatch._run_qa_retry_loop) has regenerated a draft up to
    `attempts` times and still cannot clear QA on both review kinds.
    Distinct from build_needs_edit_card (which fires on the very first,
    un-retried failure): this one carries the track-changes diff between
    the original draft and the last retry attempt, so Pieter can see
    exactly what 10 automated passes already tried before commenting his
    own edit. `hollowed` is a non-blocking, best-effort signal only
    (dispatch._looks_hollowed) -- per Pieter's explicit 11 Aug ruling,
    it never gates or stops a retry, it only tells the reviewer to look
    twice at whether a retry attempt over-corrected by deleting content
    rather than fixing it."""
    body: list[dict[str, Any]] = [
        {
            "type": "TextBlock",
            "size": "Medium",
            "weight": "Bolder",
            "wrap": True,
            "text": f"Needs edit: QA retry loop exhausted ({attempts} attempts)",
        },
        {
            "type": "FactSet",
            "facts": [
                {"title": "QA task id", "value": task_id},
                {"title": "Draft task id", "value": draft_task_id},
                {"title": "Channel", "value": channel},
                {"title": "Violations remaining", "value": ", ".join(violations) or "unknown"},
                {
                    "title": "Possible over-correction",
                    "value": "yes -- review closely" if hollowed else "no",
                },
            ],
        },
        {
            "type": "TextBlock",
            "weight": "Bolder",
            "wrap": True,
            "text": "Original draft (excerpt):",
        },
        {
            "type": "TextBlock",
            "wrap": True,
            "isSubtle": True,
            "text": original_excerpt,
        },
        {
            "type": "TextBlock",
            "weight": "Bolder",
            "wrap": True,
            "text": f"After attempt {attempts} (excerpt):",
        },
        {
            "type": "TextBlock",
            "wrap": True,
            "isSubtle": True,
            "text": revised_excerpt,
        },
    ]
    if diff_text.strip():
        body.append(
            {
                "type": "TextBlock",
                "weight": "Bolder",
                "wrap": True,
                "text": "Track changes (original -> last attempt):",
            }
        )
        body.append(
            {
                "type": "TextBlock",
                "wrap": True,
                "fontType": "Monospace",
                "text": diff_text,
            }
        )
    body.append(
        {
            "type": "TextBlock",
            "wrap": True,
            "isSubtle": True,
            "text": (
                "Comment your own edit and re-run the approval loop, or route to the "
                "approval-gate subsystem for a manual decision."
            ),
        }
    )
    content: dict[str, Any] = {
        "$schema": ADAPTIVE_CARD_SCHEMA,
        "type": "AdaptiveCard",
        "version": ADAPTIVE_CARD_VERSION,
        "body": body,
    }
    if review_url:
        content["actions"] = [
            {"type": OPEN_URL_ACTION, "title": "Review in console", "url": review_url}
        ]
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": ADAPTIVE_CARD_CONTENT_TYPE,
                "contentUrl": None,
                "content": content,
            }
        ],
    }


def notify_retry_exhausted(
    *,
    task_id: str,
    draft_task_id: str,
    channel: str,
    violations: list[str],
    original_excerpt: str,
    revised_excerpt: str,
    diff_text: str,
    attempts: int,
    hollowed: bool,
    webhook_url: str | None = None,
    review_url: str | None = None,
    http_post: Callable[..., Any] | None = None,
) -> bool:
    """Posts the Phase 2 'retries exhausted' Adaptive Card -- see
    build_retry_exhausted_card's docstring. Same AC-25 flag-gate pattern
    as notify_brief_ready/notify_needs_edit: no-ops with zero POSTs when
    TEAMS_WEBHOOK_URL is unset, never raises (a notification failure must
    never affect QA-gate correctness -- the retry loop has already
    finalized both review tasks' terminal DB state by the time this is
    called).

    `review_url` links off the QA task_id (not draft_task_id) -- GET
    /tasks/{task_id}/review resolves the draft via the same lineage walk
    qa_review_handler itself uses, so task_id is the correct anchor
    regardless of which loop position produced the draft. See F-TEAMS-
    CARD-REVIEW-LINK."""
    resolved = webhook_url if webhook_url is not None else teams_webhook_url()
    if not resolved:
        log_event(
            logger,
            logging.INFO,
            "teams_notify_retry_exhausted_skipped_no_webhook",
            task_id=task_id,
            draft_task_id=draft_task_id,
        )
        return False

    if http_post is None:
        import httpx

        http_post = httpx.post

    resolved_review_url = review_url
    if resolved_review_url is None:
        base = console_base_url()
        resolved_review_url = _review_url(base, task_id) if base else None

    card = build_retry_exhausted_card(
        task_id=task_id,
        draft_task_id=draft_task_id,
        channel=channel,
        violations=violations,
        original_excerpt=original_excerpt,
        revised_excerpt=revised_excerpt,
        diff_text=diff_text,
        attempts=attempts,
        hollowed=hollowed,
        review_url=resolved_review_url,
    )
    try:
        http_post(resolved, json=card, timeout=10.0)
    except Exception as exc:  # noqa: BLE001 - a Teams-posting failure must never break the loop
        log_event(
            logger, logging.WARNING, "teams_notify_retry_exhausted_post_failed", error=str(exc)
        )
    return True


def notify_needs_edit(
    *,
    task_id: str,
    channel: str,
    violations: list[str],
    draft_excerpt: str,
    webhook_url: str | None = None,
    review_url: str | None = None,
    http_post: Callable[..., Any] | None = None,
) -> bool:
    """Posts a 'needs edit' Adaptive Card when Brand Steward QA blocks a
    draft (Proposal C, qa-feedback-loop-proposal-2026-08-05.md). Same
    AC-25 flag-gate pattern as notify_brief_ready: no-ops with zero POSTs
    when TEAMS_WEBHOOK_URL is unset. Never raises -- a notification
    failure must never affect QA-gate correctness.

    `review_url` is injectable for tests; production callers leave it
    unset and this resolves it from CMOS_CONSOLE_BASE_URL (F-TEAMS-CARD-
    REVIEW-LINK) the same way webhook_url resolves from TEAMS_WEBHOOK_URL."""
    resolved = webhook_url if webhook_url is not None else teams_webhook_url()
    if not resolved:
        log_event(
            logger, logging.INFO, "teams_notify_needs_edit_skipped_no_webhook", task_id=task_id
        )
        return False

    if http_post is None:
        import httpx

        http_post = httpx.post

    resolved_review_url = review_url
    if resolved_review_url is None:
        base = console_base_url()
        resolved_review_url = _review_url(base, task_id) if base else None

    card = build_needs_edit_card(
        task_id=task_id,
        channel=channel,
        violations=violations,
        draft_excerpt=draft_excerpt,
        review_url=resolved_review_url,
    )
    try:
        http_post(resolved, json=card, timeout=10.0)
    except Exception as exc:  # noqa: BLE001 - a Teams-posting failure must never break the loop
        log_event(logger, logging.WARNING, "teams_notify_needs_edit_post_failed", error=str(exc))
    return True


def notify_options_digest(
    *,
    digest: dict[str, Any],
    card_count: int,
    webhook_url: str | None = None,
    http_post: Callable[..., Any] | None = None,
) -> bool:
    """Posts the Friday options digest (Appendix D PR 5, Fn 117's
    route_digest_handler). `digest` is already the full Adaptive Card
    message — services/options_inbox/teams_render.render_digest's return
    value — this function does no rendering of its own, only the POST,
    matching notify_brief_ready/notify_needs_edit's identical no-op-when-
    unset / never-raises contract. `card_count` is carried through only
    for the log line; it plays no part in whether the POST happens."""
    resolved = webhook_url if webhook_url is not None else teams_webhook_url()
    if not resolved:
        log_event(
            logger,
            logging.INFO,
            "teams_notify_options_digest_skipped_no_webhook",
            card_count=card_count,
        )
        return False

    if http_post is None:
        import httpx

        http_post = httpx.post

    try:
        http_post(resolved, json=digest, timeout=10.0)
    except Exception as exc:  # noqa: BLE001 - a Teams-posting failure must never break the loop
        log_event(
            logger, logging.WARNING, "teams_notify_options_digest_post_failed", error=str(exc)
        )
    return True
