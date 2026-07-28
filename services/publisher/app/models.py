"""Publisher request/response models."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PublishRequest(BaseModel):
    """A publish attempt.

    `asset_bytes_b64` carries the EXACT bytes to be published. Publisher
    recomputes their hash itself (app/hashing.py) and compares it with the
    hash bound into the gate token — no caller-supplied hash and no read
    of the nullable assets.content_hash column is trusted.
    """

    agent_run_id: str
    function_id: str
    asset_bytes_b64: str = Field(
        description="base64 of the raw asset bytes being published (decoded, never re-encoded)."
    )
    gate_token: str | None = None
    asset_id: str | None = None


class PublishResponse(BaseModel):
    attempt_id: str
    outcome: str
    reason: str
    function_id: str | None = None
    content_hash: str | None = None
    gate_decision_id: str | None = None
    jti: str | None = None
    vault_record_id: str | None = None


class PublishAttemptRecord(BaseModel):
    id: str
    agent_run_id: str | None = None
    gate_decision_id: str | None = None
    function_id: str | None = None
    jti: str | None = None
    content_hash: str | None = None
    outcome: str
    reason: str
    created_at: str | None = None
