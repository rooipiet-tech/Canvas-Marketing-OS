"""Single shared audit-write path for all 3 audit-emitting code paths:
taxonomy rejection (vault/routers/objects.py), consent rejection
(vault/consent.py), and retention deletion (vault/retention.py).

Because every one of those call sites goes through this one function,
the JSON log line and the vault_internal.audit_log row are structurally
identical by construction (same field set) — see .loop/spec.json AC-016.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import asyncpg

logger = logging.getLogger("vault.audit")
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

# The exact field set shared by every audit_log row and every audit JSON
# log line. Keep vault_internal.audit_log's columns and this list in sync.
AUDIT_FIELDS = [
    "correlation_id",
    "event_type",
    "object_table",
    "object_id",
    "data_subject_ref",
    "reason",
    "actor",
    "detail",
    "occurred_at",
]


async def write_audit(
    conn: asyncpg.Connection,
    *,
    event_type: str,
    object_table: str | None = None,
    object_id: str | None = None,
    data_subject_ref: str | None = None,
    reason: str | None = None,
    actor: str | None = None,
    detail: dict[str, Any] | None = None,
    correlation_id: str | None = None,
) -> dict[str, Any]:
    """Writes one vault_internal.audit_log row and emits one matching JSON
    log line. Returns the record actually written (with generated ids)."""
    correlation_id = correlation_id or str(uuid.uuid4())
    occurred_at = datetime.now(timezone.utc)
    detail = detail or {}

    record = {
        "correlation_id": correlation_id,
        "event_type": event_type,
        "object_table": object_table,
        "object_id": object_id,
        "data_subject_ref": data_subject_ref,
        "reason": reason,
        "actor": actor,
        "detail": detail,
        "occurred_at": occurred_at.isoformat(),
    }

    await conn.execute(
        """
        INSERT INTO vault_internal.audit_log
            (correlation_id, event_type, object_table, object_id,
             data_subject_ref, reason, actor, detail, occurred_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9)
        """,
        uuid.UUID(correlation_id),
        event_type,
        object_table,
        uuid.UUID(object_id) if object_id else None,
        data_subject_ref,
        reason,
        actor,
        json.dumps(detail),
        occurred_at,
    )

    logger.info(json.dumps(record, default=str))
    return record
