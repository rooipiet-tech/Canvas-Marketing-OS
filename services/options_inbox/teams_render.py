"""Adaptive Card renderer for option digests.

Extends the two-button Approve/Reject card that ca-gatekeeper-approval already
posts to `OS Approvals`. Each card section has up to three Action.OpenUrl buttons
(one per option) plus a 'Reject all' submit with a rejection-code picker. URLs
point at the existing APPROVAL_BASE_URL and carry the signed decision payload the
gatekeeper already verifies.
"""

from __future__ import annotations

from typing import Any

REJECTION_CODES = [
    "off_brand_voice",
    "claim_unsupported",
    "client_identifiable",
    "wrong_audience",
    "wrong_timing",
    "options_not_distinct",
    "too_generic",
    "factual_error",
    "legal_or_privacy_concern",
    "strategic_disagreement",
    "prefer_none_this_week",
    "other",
]


def _option_block(card: dict[str, Any], opt: dict[str, Any]) -> dict[str, Any]:
    rec = " (recommended)" if opt["option_id"] == card["recommended_option_id"] else ""
    novel = (
        " - NEW STANCE, not previously said"
        if card.get("novel_stance") and not opt.get("evidence_refs")
        else ""
    )
    return {
        "type": "Container",
        "items": [
            {
                "type": "TextBlock",
                "weight": "Bolder",
                "text": f"{opt['option_id']}. {opt['label']}{rec}{novel}",
            },
            {"type": "TextBlock", "wrap": True, "text": opt["summary"]},
            {
                "type": "TextBlock",
                "wrap": True,
                "isSubtle": True,
                "size": "Small",
                "text": (
                    f"Differs on: {opt.get('distinctness_axis', '-')} · "
                    f"Expect: {opt['predicted_outcome']}"
                ),
            },
        ],
    }


def render_card_section(card: dict[str, Any], approval_base_url: str, sig: str) -> dict[str, Any]:
    default = card.get("default_on_timeout")
    footer = (
        f"If unanswered by {card['expires_at'][:16]}Z the recommended option ({default}) applies."
        if default
        else f"Expires {card['expires_at'][:16]}Z. No default - this decision is yours."
    )
    body = [
        {"type": "TextBlock", "size": "Medium", "weight": "Bolder", "text": card["title"]},
        {"type": "TextBlock", "wrap": True, "text": card["decision_question"]},
        {
            "type": "TextBlock",
            "wrap": True,
            "isSubtle": True,
            "size": "Small",
            "text": (
                f"{card['kind']} · L{card['autonomy_level']} · {card['risk_tier']} · "
                f"replaces {', '.join(card['register_rows'])}"
            ),
        },
    ]
    body += [_option_block(card, o) for o in card["options"]]
    if card.get("recommendation_rationale"):
        body.append(
            {
                "type": "TextBlock",
                "wrap": True,
                "text": f"Why {card['recommended_option_id']}: {card['recommendation_rationale']}",
            }
        )
    body.append(
        {"type": "TextBlock", "wrap": True, "isSubtle": True, "size": "Small", "text": footer}
    )
    body.append(
        {
            "type": "Input.ChoiceSet",
            "id": f"reject_code_{card['card_id']}",
            "style": "compact",
            "placeholder": "Reject all - why?",
            "choices": [{"title": c.replace("_", " "), "value": c} for c in REJECTION_CODES],
        }
    )
    actions = [
        {
            "type": "Action.OpenUrl",
            "title": f"Choose {o['option_id']}",
            "url": (
                f"{approval_base_url}/decide?card={card['card_id']}"
                f"&opt={o['option_id']}&sig={sig}"
            ),
        }
        for o in card["options"]
    ]
    actions.append(
        {
            "type": "Action.Submit",
            "title": "Reject all",
            "data": {"card_id": card["card_id"], "outcome": "rejected_all", "sig": sig},
        }
    )
    return {
        "type": "Container",
        "separator": True,
        "items": body + [{"type": "ActionSet", "actions": actions}],
    }


def render_digest(
    cards: list[dict[str, Any]],
    *,
    approval_base_url: str,
    signer,
    overflow_count: int = 0,
    digest_date: str = "",
) -> dict[str, Any]:
    sections = [render_card_section(c, approval_base_url, signer(c["card_id"])) for c in cards]
    header = [
        {
            "type": "TextBlock",
            "size": "Large",
            "weight": "Bolder",
            "text": f"CMOS options digest - {digest_date}",
        },
        {
            "type": "TextBlock",
            "isSubtle": True,
            "text": f"{len(cards)} decisions · {overflow_count} queued",
        },
    ]
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.5",
                    "body": header + sections,
                },
            }
        ],
    }
