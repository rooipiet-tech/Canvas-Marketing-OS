"""Adaptive Card renderer for option digests.

Extends the two-button Approve/Reject card that ca-gatekeeper-approval already
posts to `OS Approvals`. Each card section has up to three Action.OpenUrl buttons
(one per option) plus a 'Reject all' Action.OpenUrl. URLs point at the existing
APPROVAL_BASE_URL and carry the signature app/option_link_sig.py verifies.

Every action here is Action.OpenUrl, deliberately — see
services/gatekeeper/app/teams_client.py's own header comment (AC-04, AC-23,
AC-26): this repo does not use Adaptive Card Action.Submit for approve/reject,
since a submit-style postback would make "who clicked" a claim of the card
payload rather than an authenticated identity, and would need a registered
Teams bot (Bot Framework) this repo does not run. An earlier version of this
renderer paired "Reject all" with an Input.ChoiceSet rejection-code picker
behind Action.Submit; that could never have worked without a bot backend to
receive it, and violated the same rule the older gate_decisions flow is
built around. "Reject all" now carries a fixed `rejection_code` of "other"
(app/routers/option_decide.py's DEFAULT_REJECTION_CODE) instead — a real
rejection-code picker is a `console_inbox`-channel feature: a genuine
authenticated web form, not bound by Teams' OpenUrl-only constraint, calling
this same /decide endpoint server-side.
"""

from __future__ import annotations

from typing import Any


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
            "type": "Action.OpenUrl",
            "title": "Reject all",
            "url": (
                f"{approval_base_url}/decide?card={card['card_id']}"
                f"&outcome=rejected_all&sig={sig}"
            ),
        }
    )
    return {
        "type": "Container",
        "separator": True,
        "items": [*body, {"type": "ActionSet", "actions": actions}],
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
