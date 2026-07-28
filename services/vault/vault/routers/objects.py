"""Generic CRUD router factory for the 9 Vault object types.

Shared across all 9 types:
  * taxonomy validation (422 naming the exact missing/invalid field, with
    an audit_log row — AC-002)
  * consent gating for client-derived writes (403 + audit on rejection,
    consent_linkage on match — AC-004)
  * a single batched multi-row INSERT (object row + object_taxonomy +
    retention_policy in one statement, via a writable CTE) per create,
    so a 1,000-object bulk insert stays within budget (AC-013)
  * taxonomy-field post-create immutability on PATCH (422 — plan F5)
  * utilisation access logging on every GET (AC-008)

`assets` additionally handles content-addressed blob storage
(content_base64 <-> vault/storage.py) — see build_assets_router() below,
which reuses every one of the shared helpers above.
"""

from __future__ import annotations

import uuid
from typing import Any

import asyncpg
from fastapi import APIRouter, Body, Header, HTTPException, Query

from .. import storage
from ..audit import write_audit
from ..consent import find_active_consent, link_consent
from ..db import get_pool
from ..models import (
    CONSENT_STATUSES,
    EVIDENCE_GRADES,
    RETENTION_CLASSES,
    TAXONOMY_FIELDS,
    ObjectTypeConfig,
    cast_for_asyncpg,
)
from ..retention import compute_expires_at
from ..rollup import record_access

CLIENT_DERIVED_FIELDS = {"data_subject_ref", "consent_channel", "consent_purpose"}


def _error(status_code: int, message: str, code: str, field: str | None = None) -> HTTPException:
    body: dict[str, Any] = {"error": {"message": message, "code": code}}
    if field:
        body["error"]["field"] = field
    return HTTPException(status_code=status_code, detail=body)


async def validate_taxonomy(
    conn: asyncpg.Connection, config: ObjectTypeConfig, payload: dict
) -> None:
    """Raises a 422 HTTPException (with an audit_log row already written)
    for the first missing/invalid taxonomy field found."""
    for field_name in TAXONOMY_FIELDS:
        value = payload.get(field_name)
        if value is None or value == "":
            await write_audit(
                conn,
                event_type="taxonomy_rejected",
                object_table=config.name,
                reason=f"missing required taxonomy field: {field_name}",
                detail={"field": field_name},
            )
            raise _error(
                422,
                f"missing required taxonomy field: {field_name}",
                "taxonomy_field_missing",
                field_name,
            )

    if payload["evidence_grade"] not in EVIDENCE_GRADES:
        await write_audit(
            conn,
            event_type="taxonomy_rejected",
            object_table=config.name,
            reason="invalid evidence_grade",
            detail={"field": "evidence_grade"},
        )
        raise _error(422, "invalid evidence_grade", "taxonomy_field_invalid", "evidence_grade")
    if payload["consent_status"] not in CONSENT_STATUSES:
        await write_audit(
            conn,
            event_type="taxonomy_rejected",
            object_table=config.name,
            reason="invalid consent_status",
            detail={"field": "consent_status"},
        )
        raise _error(422, "invalid consent_status", "taxonomy_field_invalid", "consent_status")
    if payload["retention_class"] not in RETENTION_CLASSES:
        await write_audit(
            conn,
            event_type="taxonomy_rejected",
            object_table=config.name,
            reason="invalid retention_class",
            detail={"field": "retention_class"},
        )
        raise _error(422, "invalid retention_class", "taxonomy_field_invalid", "retention_class")
    try:
        uuid.UUID(str(payload["campaign"]))
    except (ValueError, TypeError):
        await write_audit(
            conn,
            event_type="taxonomy_rejected",
            object_table=config.name,
            reason="campaign is not a valid uuid",
            detail={"field": "campaign"},
        )
        raise _error(422, "campaign must be a valid uuid", "taxonomy_field_invalid", "campaign")


def validate_required_business_fields(config: ObjectTypeConfig, payload: dict) -> None:
    for f in config.fields:
        if f.required and (f.name not in payload or payload[f.name] in (None, "")):
            raise _error(422, f"missing required field: {f.name}", "field_missing", f.name)


async def handle_consent_gate(
    conn: asyncpg.Connection, config: ObjectTypeConfig, payload: dict
) -> uuid.UUID | None:
    """Returns the matched consent_register.id if this is a client-derived
    write with an active matching consent row, None if not client-derived
    at all, or raises 403 (with audit) if client-derived with no match."""
    data_subject_ref = payload.get("data_subject_ref")
    if not data_subject_ref:
        return None
    channel = payload.get("consent_channel")
    purpose = payload.get("consent_purpose")
    if not channel or not purpose:
        raise _error(
            422,
            "consent_channel and consent_purpose are required when data_subject_ref is present",
            "consent_fields_missing",
        )
    consent_id = await find_active_consent(
        conn, data_subject_ref=data_subject_ref, channel=channel, purpose=purpose
    )
    if consent_id is None:
        await write_audit(
            conn,
            event_type="consent_rejected",
            object_table=config.name,
            data_subject_ref=data_subject_ref,
            reason="no active (non-revoked) consent_register match for subject/channel/purpose",
            detail={"channel": channel, "purpose": purpose},
        )
        raise _error(
            403, "no active consent for data_subject_ref/channel/purpose", "consent_required"
        )
    return consent_id


class _ParamBuilder:
    def __init__(self) -> None:
        self.params: list[Any] = []

    def add(self, value: Any) -> str:
        self.params.append(value)
        return f"${len(self.params)}"


async def insert_object(
    conn: asyncpg.Connection,
    config: ObjectTypeConfig,
    payload: dict,
    *,
    extra_columns: dict[str, Any] | None = None,
) -> asyncpg.Record:
    """Batched single-statement create: object row + object_taxonomy row +
    retention_policy row, via a writable-CTE INSERT (AC-013 perf)."""
    pb = _ParamBuilder()
    columns: list[str] = []
    placeholders: list[str] = []

    if config.has_campaign_column:
        columns.append("campaign_id")
        placeholders.append(pb.add(cast_for_asyncpg("uuid", payload["campaign"])))

    for f in config.fields:
        value = payload.get(f.name, f.resolve_default())
        columns.append(f.name)
        placeholders.append(pb.add(cast_for_asyncpg(f.sql_type, value)))

    if extra_columns:
        for key, value in extra_columns.items():
            columns.append(key)
            placeholders.append(pb.add(value))

    obj_insert = (
        f"INSERT INTO {config.name} ({', '.join(columns)}) "
        f"VALUES ({', '.join(placeholders)}) RETURNING *"
    )

    # The "campaigns" object type has no external campaign to reference —
    # its own taxonomy "campaign" field self-references the row being
    # created (obj.id), resolved entirely within this one statement
    # (see services/vault/migrations/0001_vault_internal_init.sql header
    # and contracts/vault-api.yaml TaxonomyFields.campaign description).
    if config.name == "campaigns":
        tax_campaign_expr = "obj.id"
    else:
        tax_campaign_expr = pb.add(cast_for_asyncpg("uuid", payload["campaign"]))

    object_table_ph = pb.add(config.name)
    vertical_ph = pb.add(payload["vertical"])
    function_id_ph = pb.add(payload["function_id"])
    evidence_grade_ph = pb.add(payload["evidence_grade"])
    consent_status_ph = pb.add(payload["consent_status"])
    retention_class_ph = pb.add(payload["retention_class"])
    expires_at_ph = pb.add(compute_expires_at(payload["retention_class"]))

    tax_columns = (
        "object_table, object_id, vertical, function_id, "
        "campaign_id, evidence_grade, consent_status, retention_class"
    )
    tax_select = (
        f"{object_table_ph}, obj.id, {vertical_ph}, {function_id_ph}, "
        f"{tax_campaign_expr}, {evidence_grade_ph}, {consent_status_ph}, {retention_class_ph}"
    )
    sql = f"""
    WITH obj AS (
        {obj_insert}
    ),
    tax AS (
        INSERT INTO vault_internal.object_taxonomy ({tax_columns})
        SELECT {tax_select}
        FROM obj
        RETURNING object_id
    ),
    ret AS (
        INSERT INTO vault_internal.retention_policy
            (object_table, object_id, retention_class, expires_at)
        SELECT {object_table_ph}, obj.id, {retention_class_ph}, {expires_at_ph}
        FROM obj
        RETURNING object_id
    )
    SELECT * FROM obj
    """
    try:
        return await conn.fetchrow(sql, *pb.params)
    except asyncpg.ForeignKeyViolationError as exc:
        raise _error(422, f"referenced id does not exist: {exc}", "fk_violation") from exc


def _row_to_dict(row: asyncpg.Record) -> dict[str, Any]:
    d = dict(row)
    taxonomy_campaign_id = d.pop("taxonomy_campaign_id", None)
    d["campaign"] = str(taxonomy_campaign_id) if taxonomy_campaign_id is not None else None
    return d


async def fetch_object(
    conn: asyncpg.Connection, config: ObjectTypeConfig, object_id: uuid.UUID
) -> dict | None:
    sql = f"""
        SELECT o.*, t.vertical, t.function_id, t.campaign_id AS taxonomy_campaign_id,
               t.evidence_grade, t.consent_status, t.retention_class
        FROM {config.name} o
        LEFT JOIN vault_internal.object_taxonomy t
          ON t.object_table = $1 AND t.object_id = o.id
        WHERE o.id = $2
    """
    row = await conn.fetchrow(sql, config.name, object_id)
    if row is None:
        return None
    return _row_to_dict(row)


async def list_objects(
    conn: asyncpg.Connection, config: ObjectTypeConfig, limit: int, offset: int
) -> list[dict]:
    sql = f"""
        SELECT o.*, t.vertical, t.function_id, t.campaign_id AS taxonomy_campaign_id,
               t.evidence_grade, t.consent_status, t.retention_class
        FROM {config.name} o
        LEFT JOIN vault_internal.object_taxonomy t
          ON t.object_table = $1 AND t.object_id = o.id
        ORDER BY o.created_at DESC
        LIMIT $2 OFFSET $3
    """
    rows = await conn.fetch(sql, config.name, limit, offset)
    return [_row_to_dict(r) for r in rows]


async def assert_taxonomy_immutable(
    conn: asyncpg.Connection, config: ObjectTypeConfig, object_id: uuid.UUID, patch_payload: dict
) -> None:
    touched = [f for f in TAXONOMY_FIELDS if f in patch_payload]
    if not touched:
        return
    current = await conn.fetchrow(
        """
        SELECT vertical, function_id, campaign_id, evidence_grade, consent_status, retention_class
        FROM vault_internal.object_taxonomy
        WHERE object_table = $1 AND object_id = $2
        """,
        config.name,
        object_id,
    )
    if current is None:
        return
    current_map = {
        "vertical": current["vertical"],
        "function_id": current["function_id"],
        "campaign": str(current["campaign_id"]) if current["campaign_id"] is not None else None,
        "evidence_grade": current["evidence_grade"],
        "consent_status": current["consent_status"],
        "retention_class": current["retention_class"],
    }
    for f in touched:
        if str(patch_payload[f]) != str(current_map.get(f)):
            raise _error(
                422, f"taxonomy field '{f}' is immutable post-create", "taxonomy_field_immutable", f
            )


async def update_object(
    conn: asyncpg.Connection, config: ObjectTypeConfig, object_id: uuid.UUID, patch_payload: dict
) -> dict | None:
    await assert_taxonomy_immutable(conn, config, object_id, patch_payload)

    patchable_names = set(config.patchable_field_names())
    updates = {k: v for k, v in patch_payload.items() if k in patchable_names}

    if updates:
        pb = _ParamBuilder()
        set_clauses = []
        field_by_name = {f.name: f for f in config.fields}
        for key, value in updates.items():
            f = field_by_name[key]
            set_clauses.append(f"{key} = {pb.add(cast_for_asyncpg(f.sql_type, value))}")
        if config.has_updated_at:
            set_clauses.append("updated_at = now()")
        obj_id_ph = pb.add(object_id)
        sql = (
            f"UPDATE {config.name} SET {', '.join(set_clauses)} WHERE id = {obj_id_ph} RETURNING id"
        )
        row = await conn.fetchrow(sql, *pb.params)
        if row is None:
            return None

    return await fetch_object(conn, config, object_id)


def build_object_router(config: ObjectTypeConfig) -> APIRouter:
    router = APIRouter()

    @router.post("", status_code=201)
    async def create(payload: dict = Body(...)):  # noqa: ANN202
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                await validate_taxonomy(conn, config, payload)
                validate_required_business_fields(config, payload)
                consent_id = await handle_consent_gate(conn, config, payload)
                row = await insert_object(conn, config, payload)
                object_id = row["id"]
                if consent_id is not None:
                    await link_consent(
                        conn,
                        object_table=config.name,
                        object_id=object_id,
                        consent_register_id=consent_id,
                    )
            return await fetch_object(conn, config, object_id)

    @router.get("")
    async def list_(limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)):  # noqa: ANN202
        pool = await get_pool()
        async with pool.acquire() as conn:
            return await list_objects(conn, config, limit, offset)

    @router.get("/{id}")
    async def get_one(id: uuid.UUID, x_caller_service: str | None = Header(default=None)):  # noqa: ANN202
        pool = await get_pool()
        async with pool.acquire() as conn:
            obj = await fetch_object(conn, config, id)
            if obj is None:
                raise _error(404, "not found", "not_found")
            await record_access(
                conn, object_table=config.name, object_id=str(id), caller_service=x_caller_service
            )
            return obj

    if config.allow_patch:

        @router.patch("/{id}")
        async def patch_one(id: uuid.UUID, payload: dict = Body(...)):  # noqa: ANN202
            pool = await get_pool()
            async with pool.acquire() as conn:
                async with conn.transaction():
                    obj = await update_object(conn, config, id, payload)
            if obj is None:
                raise _error(404, "not found", "not_found")
            return obj

    if config.allow_delete:

        @router.delete("/{id}", status_code=204)
        async def delete_one(id: uuid.UUID):  # noqa: ANN202
            pool = await get_pool()
            async with pool.acquire() as conn:
                await conn.execute(f"DELETE FROM {config.name} WHERE id = $1", id)
            return None

    return router


def build_assets_router(config: ObjectTypeConfig) -> APIRouter:
    """assets needs bespoke handling for content_base64 <-> content-addressed
    blob storage (vault/storage.py), on top of the same shared taxonomy /
    consent / immutability machinery every other type uses."""
    import base64

    router = APIRouter()

    @router.post("", status_code=201)
    async def create(payload: dict = Body(...)):  # noqa: ANN202
        content_b64 = payload.get("content_base64")
        if not content_b64:
            raise _error(
                422, "missing required field: content_base64", "field_missing", "content_base64"
            )
        try:
            raw = base64.b64decode(content_b64, validate=True)
        except Exception as exc:  # noqa: BLE001
            raise _error(
                422, "content_base64 is not valid base64", "field_invalid", "content_base64"
            ) from exc

        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                await validate_taxonomy(conn, config, payload)
                validate_required_business_fields(config, payload)
                digest, storage_uri, _deduped = await _store_blob(raw)
                row = await insert_object(
                    conn,
                    config,
                    payload,
                    extra_columns={"content_hash": digest, "storage_uri": storage_uri},
                )
                object_id = row["id"]
            obj = await fetch_object(conn, config, object_id)
        obj["content_base64"] = content_b64
        return obj

    @router.get("")
    async def list_(limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)):  # noqa: ANN202
        pool = await get_pool()
        async with pool.acquire() as conn:
            return await list_objects(conn, config, limit, offset)

    @router.get("/{id}")
    async def get_one(id: uuid.UUID, x_caller_service: str | None = Header(default=None)):  # noqa: ANN202
        pool = await get_pool()
        async with pool.acquire() as conn:
            obj = await fetch_object(conn, config, id)
            if obj is None:
                raise _error(404, "not found", "not_found")
            await record_access(
                conn, object_table=config.name, object_id=str(id), caller_service=x_caller_service
            )
        content = await _read_blob(obj["content_hash"])
        obj["content_base64"] = base64.b64encode(content).decode("ascii")
        return obj

    @router.patch("/{id}")
    async def patch_one(id: uuid.UUID, payload: dict = Body(...)):  # noqa: ANN202
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                obj = await update_object(conn, config, id, payload)
        if obj is None:
            raise _error(404, "not found", "not_found")
        return obj

    @router.delete("/{id}", status_code=204)
    async def delete_one(id: uuid.UUID):  # noqa: ANN202
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM assets WHERE id = $1", id)
        return None

    return router


async def _store_blob(raw: bytes) -> tuple[str, str, bool]:
    import asyncio

    return await asyncio.to_thread(storage.store_content_addressed, raw)


async def _read_blob(digest: str) -> bytes:
    import asyncio

    return await asyncio.to_thread(storage.read_content, digest)
