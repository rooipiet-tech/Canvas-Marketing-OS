"""Consent gating for client-derived writes (AC-004).

A create request carrying `data_subject_ref` is treated as client-derived
personal data. It is rejected (with an audit entry, via vault/audit.py)
unless a matching public.consent_register row exists with
revoked_at IS NULL for that data_subject_ref/channel/purpose at request
time. On a match, the accepted write is durably linked to the matched
consent_register row via a vault_internal.consent_linkage row.
"""

from __future__ import annotations

import uuid

import asyncpg


async def find_active_consent(
    conn: asyncpg.Connection,
    *,
    data_subject_ref: str,
    channel: str,
    purpose: str,
) -> uuid.UUID | None:
    row = await conn.fetchrow(
        """
        SELECT id FROM consent_register
        WHERE data_subject_ref = $1
          AND channel = $2
          AND purpose = $3
          AND revoked_at IS NULL
        ORDER BY consented_at DESC
        LIMIT 1
        """,
        data_subject_ref,
        channel,
        purpose,
    )
    return row["id"] if row else None


async def link_consent(
    conn: asyncpg.Connection,
    *,
    object_table: str,
    object_id: uuid.UUID,
    consent_register_id: uuid.UUID,
) -> None:
    await conn.execute(
        """
        INSERT INTO vault_internal.consent_linkage
            (object_table, object_id, consent_register_id)
        VALUES ($1, $2, $3)
        ON CONFLICT (object_table, object_id) DO NOTHING
        """,
        object_table,
        object_id,
        consent_register_id,
    )
