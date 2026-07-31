"""Gatekeeper-owned approval inbox and approval-link lifecycle.

Every approval-required (level 1/2) decision writes a
governance.approval_inbox row — whether or not a Teams webhook is
configured. The row is what makes the Approve/Reject deep link
SINGLE-USE and TIME-BOUNDED (AC-33): the link carries only an opaque
token, and the row records whether that token has been consumed and when
it expires. Possession of the link never determines the approver — that
comes from the Easy-Auth principal on the request (AC-32).

Link tokens use `secrets.token_urlsafe`, whose alphabet is
[A-Za-z0-9_-] only. Raw base64 would emit '+', '/' and '=' which are
URI-significant and break the link a large fraction of the time (L-0004).

When no teams-webhook-url is configured — the actual current state of
cmos-dev's Key Vault — the inbox row IS the delivery mechanism, and is the
primary end-to-end-tested path this session (AC-05).
"""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import quote

from app.config import approval_base_url, approval_link_ttl_seconds, teams_webhook_url
from app.teams_client import build_adaptive_card, post_adaptive_card

APPROVAL_ROUTE_TEAMS = "teams"
APPROVAL_ROUTE_INBOX = "inbox"

CHOICE_APPROVE = "approve"
CHOICE_REJECT = "reject"

# 32 bytes -> 43 URL-safe characters. No '+', '/' or '=' ever (L-0004).
LINK_TOKEN_BYTES = 32

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_EXPIRED = "expired"

OUTCOME_APPROVED = "approved"
OUTCOME_REJECTED = "rejected"
OUTCOME_LINK_EXPIRED = "link_expired"
OUTCOME_LINK_ALREADY_USED = "link_already_used"

_INSERT_INBOX = """
    INSERT INTO governance.approval_inbox (
        gate_decision_id, agent_run_id, function_id, action_class, level,
        content_hash, preview_title, preview_reference, evidence_summary,
        status, link_token, expires_at
    ) VALUES (
        %(gate_decision_id)s, %(agent_run_id)s, %(function_id)s, %(action_class)s, %(level)s,
        %(content_hash)s, %(preview_title)s, %(preview_reference)s, %(evidence_summary)s,
        'pending', %(link_token)s, %(expires_at)s
    )
    RETURNING *
"""

_SELECT_BY_TOKEN = "SELECT * FROM governance.approval_inbox WHERE link_token = %(link_token)s"

_SELECT_LATEST_APPROVED = """
    SELECT * FROM governance.approval_inbox
     WHERE agent_run_id = %(agent_run_id)s
       AND function_id = %(function_id)s
       AND content_hash IS NOT DISTINCT FROM %(content_hash)s
       AND status = 'approved'
     ORDER BY decided_at DESC NULLS LAST, created_at DESC
     LIMIT 1
"""

# Same match shape as _SELECT_LATEST_APPROVED but WITHOUT the
# status='approved' filter — used by GET /approval-status (plan step 4,
# AC-15) to report the REAL current decision status (pending / approved /
# rejected / expired), not only the approved case latest_approved() cares
# about.
_SELECT_LATEST_ANY_STATUS = """
    SELECT * FROM governance.approval_inbox
     WHERE agent_run_id = %(agent_run_id)s
       AND function_id = %(function_id)s
       AND content_hash IS NOT DISTINCT FROM %(content_hash)s
     ORDER BY created_at DESC
     LIMIT 1
"""

_CONSUME_LINK = """
    UPDATE governance.approval_inbox
       SET status = %(status)s,
           decided_by = %(decided_by)s,
           decided_at = now(),
           link_consumed_at = now(),
           gate_decision_id = COALESCE(%(gate_decision_id)s, gate_decision_id)
     WHERE id = %(inbox_id)s
       AND link_consumed_at IS NULL
    RETURNING *
"""

_MARK_EXPIRED = """
    UPDATE governance.approval_inbox
       SET status = 'expired'
     WHERE id = %(inbox_id)s
       AND status = 'pending'
    RETURNING *
"""

_INSERT_ACTION = """
    INSERT INTO governance.approval_actions (
        approval_inbox_id, gate_decision_id, outcome, reason,
        principal_id, principal_name
    ) VALUES (
        %(approval_inbox_id)s, %(gate_decision_id)s, %(outcome)s, %(reason)s,
        %(principal_id)s, %(principal_name)s
    )
    RETURNING *
"""


def generate_link_token() -> str:
    """URL-safe single-use approval-link token (never raw base64)."""
    return secrets.token_urlsafe(LINK_TOKEN_BYTES)


def approval_action_url(link_token: str, choice: str, base_url: str | None = None) -> str:
    """Deep link for the Entra-ID-protected approval-action endpoint."""
    base = (base_url or approval_base_url()).rstrip("/")
    return f"{base}/approval-action/{quote(link_token, safe='')}?choice={choice}"


def create_approval_request(
    conn,
    *,
    gate_decision_id: str | uuid.UUID | None,
    agent_run_id: str | uuid.UUID,
    function_id: str,
    action_class: str,
    level: int,
    content_hash: str | None,
    preview_title: str,
    preview_reference: str | None,
    evidence_summary: str,
    ttl_seconds: int | None = None,
) -> dict[str, Any]:
    """Create the single-use, 24h-expiring approval record."""
    ttl = approval_link_ttl_seconds() if ttl_seconds is None else int(ttl_seconds)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl)
    with conn.cursor() as cur:
        cur.execute(
            _INSERT_INBOX,
            {
                "gate_decision_id": str(gate_decision_id) if gate_decision_id else None,
                "agent_run_id": str(agent_run_id),
                "function_id": function_id,
                "action_class": action_class,
                "level": level,
                "content_hash": content_hash,
                "preview_title": preview_title,
                "preview_reference": preview_reference,
                "evidence_summary": evidence_summary,
                "link_token": generate_link_token(),
                "expires_at": expires_at,
            },
        )
        return dict(cur.fetchone())


def get_by_link_token(conn, link_token: str) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(_SELECT_BY_TOKEN, {"link_token": link_token})
        row = cur.fetchone()
    return dict(row) if row else None


def latest_approved(
    conn, *, agent_run_id: str | uuid.UUID, function_id: str, content_hash: str | None
) -> dict[str, Any] | None:
    """Most recent human approval matching this exact request, if any."""
    with conn.cursor() as cur:
        cur.execute(
            _SELECT_LATEST_APPROVED,
            {
                "agent_run_id": str(agent_run_id),
                "function_id": function_id,
                "content_hash": content_hash,
            },
        )
        row = cur.fetchone()
    return dict(row) if row else None


def latest_status(
    conn, *, agent_run_id: str | uuid.UUID, function_id: str, content_hash: str | None
) -> dict[str, Any] | None:
    """The most recent approval_inbox row matching this exact request,
    REGARDLESS of status (AC-15) — GET /approval-status's read path.
    Returns None if this (agent_run_id, function_id, content_hash) triple
    never created an approval_inbox row at all (e.g. it never reached
    autonomy level 1/2, or hasn't been seen yet)."""
    with conn.cursor() as cur:
        cur.execute(
            _SELECT_LATEST_ANY_STATUS,
            {
                "agent_run_id": str(agent_run_id),
                "function_id": function_id,
                "content_hash": content_hash,
            },
        )
        row = cur.fetchone()
    return dict(row) if row else None


def consume_link(
    conn,
    inbox_id: str | uuid.UUID,
    *,
    status: str,
    decided_by: str,
    gate_decision_id: str | uuid.UUID | None = None,
) -> dict[str, Any] | None:
    """Atomically mark the link consumed. Returns None if already consumed."""
    with conn.cursor() as cur:
        cur.execute(
            _CONSUME_LINK,
            {
                "inbox_id": str(inbox_id),
                "status": status,
                "decided_by": decided_by,
                "gate_decision_id": str(gate_decision_id) if gate_decision_id else None,
            },
        )
        row = cur.fetchone()
    return dict(row) if row else None


def mark_expired(conn, inbox_id: str | uuid.UUID) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(_MARK_EXPIRED, {"inbox_id": str(inbox_id)})
        row = cur.fetchone()
    return dict(row) if row else None


def record_approval_action(
    conn,
    *,
    approval_inbox_id: str | uuid.UUID | None,
    gate_decision_id: str | uuid.UUID | None,
    outcome: str,
    reason: str,
    principal_id: str | None,
    principal_name: str | None,
) -> dict[str, Any]:
    """One audit row per approval-link click (AC-34), four outcomes."""
    with conn.cursor() as cur:
        cur.execute(
            _INSERT_ACTION,
            {
                "approval_inbox_id": str(approval_inbox_id) if approval_inbox_id else None,
                "gate_decision_id": str(gate_decision_id) if gate_decision_id else None,
                "outcome": outcome,
                "reason": reason,
                "principal_id": principal_id,
                "principal_name": principal_name,
            },
        )
        return dict(cur.fetchone())


def dispatch_approval_request(
    approval: dict[str, Any],
    *,
    webhook_url: str | None = None,
    base_url: str | None = None,
    http_post: Callable[..., Any] | None = None,
) -> str:
    """Deliver the approval request.

    Returns APPROVAL_ROUTE_TEAMS when a Workflows webhook is configured and
    the card was posted, otherwise APPROVAL_ROUTE_INBOX (the row already
    written by create_approval_request IS the delivery).
    """
    resolved_webhook = webhook_url if webhook_url is not None else teams_webhook_url()
    if not resolved_webhook:
        return APPROVAL_ROUTE_INBOX

    card = build_adaptive_card(
        function_id=approval["function_id"],
        action_class=approval["action_class"],
        level=approval["level"],
        preview_title=approval["preview_title"],
        preview_reference=approval["preview_reference"],
        evidence_summary=approval["evidence_summary"],
        approve_url=approval_action_url(approval["link_token"], CHOICE_APPROVE, base_url),
        reject_url=approval_action_url(approval["link_token"], CHOICE_REJECT, base_url),
        expires_at=approval["expires_at"],
    )
    post_adaptive_card(resolved_webhook, card, http_post=http_post)
    return APPROVAL_ROUTE_TEAMS
