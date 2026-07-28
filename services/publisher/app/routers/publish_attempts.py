"""GET /publish-attempts/{id} — agent-native audit read-back (AC-17)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.db import get_conn
from app.models import PublishAttemptRecord

router = APIRouter(tags=["publish-attempts"])

_SELECT_ATTEMPT = """
    SELECT id, agent_run_id, gate_decision_id, function_id, jti, content_hash,
           outcome, reason, created_at
      FROM governance.publish_attempts
     WHERE id = %(attempt_id)s
"""


def _serialise(row: dict[str, Any]) -> PublishAttemptRecord:
    return PublishAttemptRecord(
        id=str(row["id"]),
        agent_run_id=str(row["agent_run_id"]) if row["agent_run_id"] else None,
        gate_decision_id=str(row["gate_decision_id"]) if row["gate_decision_id"] else None,
        function_id=row["function_id"],
        jti=row["jti"],
        content_hash=row["content_hash"],
        outcome=row["outcome"],
        reason=row["reason"],
        created_at=row["created_at"].isoformat() if row["created_at"] else None,
    )


@router.get("/publish-attempts/{attempt_id}", response_model=PublishAttemptRecord)
def get_publish_attempt(attempt_id: str, conn=Depends(get_conn)) -> PublishAttemptRecord:
    try:
        uuid.UUID(attempt_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="attempt_id must be a uuid") from exc

    with conn.cursor() as cur:
        cur.execute(_SELECT_ATTEMPT, {"attempt_id": attempt_id})
        row = cur.fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="publish attempt not found")
    return _serialise(dict(row))
