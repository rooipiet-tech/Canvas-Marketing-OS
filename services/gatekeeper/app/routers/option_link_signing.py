"""POST /sign-option-card-link — internal-only (ca-gatekeeper, main.py).

services/orchestrator's route_digest_handler (Fn 117, Appendix D PR 5)
needs the same RS256 signature app/option_link_sig.sign_card_link()
produces for every pending card's /decide links (services/options_inbox/
teams_render.py's render_digest requires a `signer` callable), but the
signing key only THIS service's identity can reach (Key Vault) lives
here, not in orchestrator.

Internal ingress only (mounted on main.py, never approval_main.py) --
this is a service-to-service call, never a human-facing one. The
signature it returns authenticates nothing about identity (see
option_link_sig.py's own docstring): it only proves a /decide link was
genuinely issued for this card_id, exactly as it does when embedded by
the digest render.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException

from app.option_link_sig import sign_card_link

router = APIRouter()


@router.post("/sign-option-card-link")
def sign_option_card_link(payload: dict = Body(...)) -> dict:
    card_id = payload.get("card_id")
    if not card_id or not isinstance(card_id, str):
        raise HTTPException(
            status_code=422,
            detail={"error": {"message": "card_id is required", "code": "invalid_body"}},
        )
    return {"card_id": card_id, "sig": sign_card_link(card_id)}
