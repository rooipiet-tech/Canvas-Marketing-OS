"""GET /decide — the option-card equivalent of approval_action.py's
GET /approval-action/{link_token}, for the ratification model's
option_cards / approval_decisions (Appendix D PR 3).

URL shape is fixed by services/options_inbox/teams_render.py, which
already renders it:

    {approval_base_url}/decide?card={card_id}&opt={option_id}&sig={sig}
    {approval_base_url}/decide?card={card_id}&outcome=rejected_all&sig={sig}

Both are Action.OpenUrl deep links — this repo does not use Adaptive Card
Action.Submit for approve/reject, deliberately (see
services/gatekeeper/app/teams_client.py's own header: a submit-style
postback would make "who clicked" a claim of the card payload rather than
an authenticated identity, and would need a registered Teams bot this
repo does not run). services/options_inbox/teams_render.py originally
rendered "Reject all" as an Action.Submit with an Input.ChoiceSet
rejection-code picker; that violated exactly this rule (and could never
have worked without a bot backend to receive it) and was corrected
alongside this endpoint to a plain Action.OpenUrl, with `rejection_code`
defaulting to "other" here. A rejection-code picker remains available on
the `console_inbox` channel, which is a real authenticated web form
calling this same endpoint server-side and is not bound by the Teams
Action.OpenUrl-only constraint.

Security model, mirrored from approval_action.py:

  * Mounted on approval_main.py only (ca-gatekeeper-approval, Entra-ID-
    authenticated, the only externally reachable governance route).
  * `decided_by` is the Easy-Auth-authenticated principal on THIS
    request (app/auth.py) — never derived from the link.
  * `sig` (app/option_link_sig.py) proves the link was genuinely issued
    for this card_id, not guessed; it authenticates nothing about which
    option is chosen, exactly as approval_action.py's `choice` query
    param is not itself signed.
  * One decision per card is enforced by the DB's own
    approval_decisions_one_per_card UNIQUE constraint
    (app/option_decisions.record_decision), the equivalent of
    approval_action.py's atomic conditional link-consume UPDATE.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.auth import principal_from_headers
from app.db import get_conn
from app.option_decisions import (
    DecisionAlreadyRecorded,
    fetch_card,
    fetch_decision,
    record_decision,
)
from app.option_link_sig import verify_card_link

router = APIRouter(tags=["option-decide"])

DEFAULT_REJECTION_CODE = "other"


def _require_principal(request: Request):
    principal = principal_from_headers(request.headers)
    if principal is None:
        raise HTTPException(
            status_code=401,
            detail=(
                "no authenticated principal on this request — the decider is "
                "taken from the Entra ID sign-in, never from possession of the link"
            ),
        )
    return principal


def _latency_seconds(card: dict[str, Any], decided_at: datetime) -> int | None:
    created_at = card.get("created_at")
    if created_at is None:
        return None
    return max(0, int((decided_at - created_at).total_seconds()))


@router.get("/decide")
def decide(
    request: Request,
    card: str = Query(..., alias="card"),
    sig: str = Query(...),
    opt: str | None = Query(None, pattern="^[A-C]$"),
    outcome: str | None = Query(None, pattern="^rejected_all$"),
    channel: str = Query("teams_card", pattern="^(teams_card|console_inbox|digest_email|system)$"),
    conn=Depends(get_conn),
) -> dict[str, Any]:
    principal = _require_principal(request)

    if not verify_card_link(card, sig):
        raise HTTPException(status_code=403, detail="signature does not match this card")

    if (opt is None) == (outcome is None):
        raise HTTPException(
            status_code=400, detail="exactly one of `opt` or `outcome=rejected_all` is required"
        )

    row = fetch_card(conn, card)
    if row is None:
        # Unknown card_id: nothing to bind an audit row to, and disclosing
        # more would turn this into a card-id oracle (same reasoning as
        # approval_action.py's unknown-link_token 404).
        raise HTTPException(status_code=404, detail="option card not found")

    now = datetime.now(timezone.utc)
    expires_at = row["expires_at"]
    if expires_at is not None and expires_at <= now:
        raise HTTPException(
            status_code=410,
            detail="this card has expired — its timeout behaviour, not this click, decides it",
        )

    if opt is not None:
        option_ids = {o["option_id"] for o in row["card"].get("options", [])}
        if opt not in option_ids:
            raise HTTPException(
                status_code=400,
                detail=f"{opt!r} is not one of this card's options {sorted(option_ids)}",
            )
        decision_kwargs: dict[str, Any] = {
            "outcome": "chosen",
            "chosen_option_id": opt,
            "rejection_code": None,
        }
    else:
        decision_kwargs = {
            "outcome": "rejected_all",
            "chosen_option_id": None,
            "rejection_code": DEFAULT_REJECTION_CODE,
        }

    try:
        decision = record_decision(
            conn,
            card=row,
            decided_by=principal.decided_by,
            channel=channel,
            latency_seconds=_latency_seconds(row, now),
            **decision_kwargs,
        )
    except DecisionAlreadyRecorded:
        existing = fetch_decision(conn, card)
        raise HTTPException(
            status_code=409,
            detail={
                "outcome": "already_decided",
                "decided_by": existing["decided_by"] if existing else None,
                "decided_at": existing["decided_at"].isoformat() if existing else None,
            },
        ) from None

    return {
        "outcome": decision["outcome"],
        "card_id": str(decision["card_id"]),
        "chosen_option_id": decision["chosen_option_id"],
        "was_recommended": decision["was_recommended"],
        "rejection_code": decision["rejection_code"],
        "decided_by": decision["decided_by"],
        "decided_at": decision["decided_at"].isoformat(),
    }
