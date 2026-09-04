"""Signs and verifies the `sig` query parameter on option-card decide
links (services/options_inbox/teams_render.py's render_card_section).

Unlike the decision-record signature (app/option_decisions.py's
build_decision_signature, contracts/approval-decision.schema.json's
`signature` field), this one authenticates only `card_id` — it proves the
link was genuinely issued by a digest run and not guessed or forged, the
same trust boundary approval_inbox.py's opaque `link_token` establishes
for the older gate_decisions flow. WHICH option a principal then chooses,
or whether they reject all, is that principal's authenticated action at
request time (app/auth.py) and carries no separate signature requirement
of its own — exactly how approval_action.py's `choice` query param is not
itself signed either.

Reuses the same RS256 Key Vault signer gate-tokens use (app/signer) so
production never introduces a second key to manage.
"""

from __future__ import annotations

import base64

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from app.signer import get_signer


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def sign_card_link(card_id: str) -> str:
    """The signature embedded in every /decide URL rendered for a card."""
    return _b64url(get_signer().sign(card_id.encode("utf-8")))


def verify_card_link(card_id: str, sig: str) -> bool:
    """True iff `sig` is card_id's signature from this session's signing key."""
    try:
        raw_sig = _b64url_decode(sig)
    except Exception:
        return False
    public_key = serialization.load_pem_public_key(get_signer().public_key_pem().encode("utf-8"))
    try:
        public_key.verify(raw_sig, card_id.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature:
        return False
    return True
