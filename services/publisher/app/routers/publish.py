"""POST /publish — verification and refusal (AC-07..AC-13; plan step 14
adds AC-08/AC-09/AC-30's Buffer dry-run/live path).

Exactly ONE governance.publish_attempts row is appended on every branch:

  no token                 -> rejected  token_absent
  bad/unpinned alg         -> rejected  invalid_alg
  expired                  -> rejected  token_expired
  malformed/forged         -> rejected  token_invalid
  kill switch active       -> rejected  kill_switch_active:<scope>[:fn]
  asset bytes changed      -> rejected  content_hash_mismatch
  asset_id supplied,
    vault lookup failed    -> rejected  vault_lookup_failed
  asset_id lookup ok,
    vault content_hash
    disagrees with bytes   -> rejected  content_hash_mismatch
  jti already consumed     -> rejected  token_replayed
  live mode, queue at cap  -> rejected  buffer_queue_cap_exceeded
  dry-run (default,
    or proof-circuit forced) -> published  published_dry_run
  live mode, all checks OK -> published  published

Ordering rationale: the kill switch is re-checked AFTER the token is
verified but BEFORE the token is consumed, so a pre-issued, still-valid
token cannot outlive an operator flipping the switch. The asset_id-based
Vault cross-check (PV3-01, step 14) runs AFTER the primary bytes-recompute
hash check but BEFORE the jti is burned, so a broken/malformed asset_id
lookup fails closed without spending the token. The jti is consumed last
so a token refused for any other reason is not burned. The Buffer
queue-cap check and create_draft call only ever happen when NOT dry-run —
dry-run (the default, and always forced for a proof-circuit-tagged asset
regardless of PUBLISHER_DRY_RUN) skips them entirely, so nothing is ever
queued.
"""

from __future__ import annotations

import base64
import binascii
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from app.buffer_client import BufferClient, BufferClientError, resolve_mcp_buffer_base_url
from app.config import (
    AGENT_NAME_LOOP_PROOF,
    BUFFER_FREE_TIER_QUEUE_CAP,
    BUFFER_LINKEDIN_CHANNEL_ID,
    allowed_algorithms,
    gate_token_public_key_pem,
    publisher_dry_run,
    token_audience,
    token_issuer,
)
from app.db import get_conn
from app.hashing import hashes_match, recompute_content_hash
from app.jti_ledger import JtiLedger
from app.kill_switch import is_blocked
from app.models import PublishRequest, PublishResponse
from app.telemetry_wiring import emit_span
from app.vault_adapter import get_vault_adapter
from app.vault_lookup import VaultLookupError, fetch_asset_and_agent_name
from app.verifier import (
    REASON_CONTENT_HASH_MISMATCH,
    REASON_TOKEN_INVALID,
    REASON_TOKEN_REPLAYED,
    GateTokenVerifier,
    VerificationError,
)

router = APIRouter(tags=["publish"])

REASON_VAULT_LOOKUP_FAILED = "vault_lookup_failed"
REASON_BUFFER_QUEUE_CAP_EXCEEDED = "buffer_queue_cap_exceeded"
REASON_PUBLISHED_DRY_RUN = "published_dry_run"
REASON_PUBLISHED = "published"

OUTCOME_PUBLISHED = "published"
OUTCOME_REJECTED = "rejected"

_INSERT_ATTEMPT = """
    INSERT INTO governance.publish_attempts (
        agent_run_id, gate_decision_id, function_id, jti, content_hash, outcome, reason
    ) VALUES (
        %(agent_run_id)s, %(gate_decision_id)s, %(function_id)s, %(jti)s,
        %(content_hash)s, %(outcome)s, %(reason)s
    )
    RETURNING id, agent_run_id, gate_decision_id, function_id, jti, content_hash,
              outcome, reason, created_at
"""


def build_verifier() -> GateTokenVerifier:
    return GateTokenVerifier(
        public_key_pem=gate_token_public_key_pem(),
        issuer=token_issuer(),
        audience=token_audience(),
        allowed_algorithms=allowed_algorithms(),
    )


def record_attempt(
    conn,
    *,
    agent_run_id: str | None,
    gate_decision_id: str | None,
    function_id: str | None,
    jti: str | None,
    content_hash: str | None,
    outcome: str,
    reason: str,
) -> dict[str, Any]:
    """Append exactly one immutable publish_attempts row."""
    with conn.cursor() as cur:
        cur.execute(
            _INSERT_ATTEMPT,
            {
                "agent_run_id": agent_run_id,
                "gate_decision_id": gate_decision_id,
                "function_id": function_id,
                "jti": jti,
                "content_hash": content_hash,
                "outcome": outcome,
                "reason": reason,
            },
        )
        return dict(cur.fetchone())


def _response(attempt: dict[str, Any], **extra: Any) -> PublishResponse:
    return PublishResponse(
        attempt_id=str(attempt["id"]),
        outcome=attempt["outcome"],
        reason=attempt["reason"],
        function_id=attempt["function_id"],
        content_hash=attempt["content_hash"],
        gate_decision_id=(
            str(attempt["gate_decision_id"]) if attempt["gate_decision_id"] else None
        ),
        jti=attempt["jti"],
        **extra,
    )


def _refuse(attempt: dict[str, Any]) -> HTTPException:
    return HTTPException(status_code=403, detail=_response(attempt).model_dump())


def _safe_uuid(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        return None


@router.post("/publish", response_model=PublishResponse)
def publish(
    request: PublishRequest, http_request: Request, conn=Depends(get_conn)
) -> PublishResponse:
    """Thin telemetry-wrapping shell (AC-03/AC-04/DE-5) around
    _publish_impl, which carries the full pre-existing verification/
    refusal logic unchanged. Kept as a separate wrapper so this adoption
    touches zero lines of the actual decision logic."""
    with emit_span(
        "publisher.publish",
        http_request.headers,
        function_id=request.function_id,
        task_ref=request.agent_run_id,
    ):
        return _publish_impl(request, conn)


def _publish_impl(request: PublishRequest, conn) -> PublishResponse:
    agent_run_id = _safe_uuid(request.agent_run_id)
    if agent_run_id is None:
        raise HTTPException(status_code=400, detail="agent_run_id must be a uuid")

    try:
        asset_bytes = base64.b64decode(request.asset_bytes_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(
            status_code=400, detail=f"asset_bytes_b64 is not base64: {exc}"
        ) from exc

    # (1) Token present + signature/alg/expiry valid.
    verifier = build_verifier()
    try:
        claims = verifier.verify(request.gate_token)
    except VerificationError as exc:
        raise _refuse(
            record_attempt(
                conn,
                agent_run_id=agent_run_id,
                gate_decision_id=None,
                function_id=request.function_id,
                jti=None,
                content_hash=None,
                outcome=OUTCOME_REJECTED,
                reason=exc.reason,
            )
        ) from exc

    jti = claims["jti"]
    gate_decision_id = _safe_uuid(claims.get("gate_decision_id"))

    try:
        bound_content_hash = verifier.bound_content_hash(claims)
        bound_function_id = verifier.bound_function_id(claims)
    except VerificationError as exc:
        raise _refuse(
            record_attempt(
                conn,
                agent_run_id=agent_run_id,
                gate_decision_id=gate_decision_id,
                function_id=request.function_id,
                jti=jti,
                content_hash=None,
                outcome=OUTCOME_REJECTED,
                reason=REASON_TOKEN_INVALID,
            )
        ) from exc

    # (2) Kill switch — re-checked here even though the token is already
    #     valid, with a direct uncached read (no cache of any TTL).
    status = is_blocked(conn, bound_function_id)
    if status.blocked:
        raise _refuse(
            record_attempt(
                conn,
                agent_run_id=agent_run_id,
                gate_decision_id=gate_decision_id,
                function_id=bound_function_id,
                jti=jti,
                content_hash=bound_content_hash,
                outcome=OUTCOME_REJECTED,
                reason=status.audit_reason,
            )
        )

    # (3) Independently recompute the hash over the raw asset bytes.
    actual_content_hash = recompute_content_hash(asset_bytes)
    if not hashes_match(bound_content_hash, actual_content_hash):
        raise _refuse(
            record_attempt(
                conn,
                agent_run_id=agent_run_id,
                gate_decision_id=gate_decision_id,
                function_id=bound_function_id,
                jti=jti,
                content_hash=actual_content_hash,
                outcome=OUTCOME_REJECTED,
                reason=REASON_CONTENT_HASH_MISMATCH,
            )
        )

    # (3.5) NEW (plan step 14, PV3-01): if the caller supplied asset_id,
    # cross-check Vault's OWN stored content_hash for that asset against
    # the just-recomputed bytes hash (ADDITIONAL to check (3) above, which
    # stays primary/unchanged), and read the asset's agent_run_id ->
    # agent_name to decide whether this publish is forced dry-run
    # (AC-30). asset_id ABSENT (every existing caller today, e.g.
    # caj-governance-smoke) skips this entirely — fully backward
    # compatible. asset_id SUPPLIED but the lookup failing/malformed
    # FAILS CLOSED (refuses, never proceeds as if it were absent).
    dry_run_forced = False
    if request.asset_id:
        try:
            lookup = fetch_asset_and_agent_name(request.asset_id)
        except VaultLookupError:
            raise _refuse(
                record_attempt(
                    conn,
                    agent_run_id=agent_run_id,
                    gate_decision_id=gate_decision_id,
                    function_id=bound_function_id,
                    jti=jti,
                    content_hash=actual_content_hash,
                    outcome=OUTCOME_REJECTED,
                    reason=REASON_VAULT_LOOKUP_FAILED,
                )
            ) from None
        if not hashes_match(lookup.content_hash, actual_content_hash):
            raise _refuse(
                record_attempt(
                    conn,
                    agent_run_id=agent_run_id,
                    gate_decision_id=gate_decision_id,
                    function_id=bound_function_id,
                    jti=jti,
                    content_hash=actual_content_hash,
                    outcome=OUTCOME_REJECTED,
                    reason=REASON_CONTENT_HASH_MISMATCH,
                )
            )
        dry_run_forced = lookup.agent_name == AGENT_NAME_LOOP_PROOF

    effective_dry_run = dry_run_forced or publisher_dry_run()

    # (4) Burn the jti last, atomically, against the durable ledger.
    if not JtiLedger(conn).consume(jti, gate_decision_id):
        raise _refuse(
            record_attempt(
                conn,
                agent_run_id=agent_run_id,
                gate_decision_id=gate_decision_id,
                function_id=bound_function_id,
                jti=jti,
                content_hash=actual_content_hash,
                outcome=OUTCOME_REJECTED,
                reason=REASON_TOKEN_REPLAYED,
            )
        )

    # (4.5) NEW (plan step 14; AC-08, AC-09, AC-30): live-mode-only Buffer
    # queue-cap check + create_draft. COMPLETELY SKIPPED when
    # effective_dry_run (the default, or a proof-circuit-forced dry-run
    # regardless of PUBLISHER_DRY_RUN's value) — no live call is ever
    # made, and the distinct 'published_dry_run' reason below is what
    # AC-08(a) calls "a distinctly-recorded 'dry-run' log/DB entry".
    if not effective_dry_run:
        with BufferClient(base_url=resolve_mcp_buffer_base_url()) as buffer:
            try:
                queue_count = buffer.list_queue_count(BUFFER_LINKEDIN_CHANNEL_ID)
            except BufferClientError:
                raise _refuse(
                    record_attempt(
                        conn,
                        agent_run_id=agent_run_id,
                        gate_decision_id=gate_decision_id,
                        function_id=bound_function_id,
                        jti=jti,
                        content_hash=actual_content_hash,
                        outcome=OUTCOME_REJECTED,
                        reason=REASON_BUFFER_QUEUE_CAP_EXCEEDED,
                    )
                ) from None
            if queue_count >= BUFFER_FREE_TIER_QUEUE_CAP:
                raise _refuse(
                    record_attempt(
                        conn,
                        agent_run_id=agent_run_id,
                        gate_decision_id=gate_decision_id,
                        function_id=bound_function_id,
                        jti=jti,
                        content_hash=actual_content_hash,
                        outcome=OUTCOME_REJECTED,
                        reason=REASON_BUFFER_QUEUE_CAP_EXCEEDED,
                    )
                )
            buffer.create_draft(
                channel_id=BUFFER_LINKEDIN_CHANNEL_ID, text=asset_bytes.decode("utf-8")
            )

    # (5) Publish: exactly one Vault-recording adapter call (unchanged —
    # this is Publisher's own internal audit adapter, distinct from the
    # Buffer call above, and fires on both the dry-run and live paths).
    record = get_vault_adapter().record_publish(
        agent_run_id=agent_run_id,
        function_id=bound_function_id,
        content_hash=actual_content_hash,
        gate_decision_id=str(gate_decision_id),
        jti=jti,
    )
    attempt = record_attempt(
        conn,
        agent_run_id=agent_run_id,
        gate_decision_id=gate_decision_id,
        function_id=bound_function_id,
        jti=jti,
        content_hash=actual_content_hash,
        outcome=OUTCOME_PUBLISHED,
        reason=REASON_PUBLISHED_DRY_RUN if effective_dry_run else REASON_PUBLISHED,
    )
    return _response(attempt, vault_record_id=record.record_id)
