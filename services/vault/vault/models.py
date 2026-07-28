"""Generic object-type registry driving the CRUD factory in
vault/routers/objects.py.

Each of the 9 Vault object types (see contracts/vault-schema/schema.sql)
is described declaratively here: which public-schema table it maps to,
its business (non-taxonomy) fields, and whether it has a real
`campaign_id` column, an `updated_at` column, and PATCH/DELETE support.
The router factory reads this registry rather than hard-coding 9 separate
route sets, so the request-validation / taxonomy / consent / persistence
logic (vault/routers/objects.py) is written once.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

# The 6 mandatory taxonomy fields, in the order they appear in
# vault_internal.object_taxonomy (contracts/vault-api.yaml TaxonomyFields).
TAXONOMY_FIELDS = [
    "vertical",
    "function_id",
    "campaign",
    "evidence_grade",
    "consent_status",
    "retention_class",
]

RETENTION_CLASSES = {"ephemeral_30d", "standard_1y", "extended_3y", "legal_hold"}
EVIDENCE_GRADES = {"A", "B", "C", "D", "unverified"}
CONSENT_STATUSES = {"granted", "revoked", "not_required", "pending"}


@dataclass(frozen=True)
class FieldSpec:
    name: str
    sql_type: str  # "text" | "jsonb" | "uuid" | "numeric" | "integer" | "timestamptz" | "enum"
    required: bool = False
    default: Any | Callable[[], Any] | None = None
    patchable: bool = False

    def resolve_default(self) -> Any:
        return self.default() if callable(self.default) else self.default


@dataclass(frozen=True)
class ObjectTypeConfig:
    name: str  # public schema table name == the value stored as object_table
    path: str  # URL path segment (contracts/vault-api.yaml)
    has_campaign_column: bool
    has_updated_at: bool
    allow_patch: bool
    allow_delete: bool
    fields: list[FieldSpec] = field(default_factory=list)

    def field_names(self) -> list[str]:
        return [f.name for f in self.fields]

    def patchable_field_names(self) -> list[str]:
        return [f.name for f in self.fields if f.patchable]


OBJECT_TYPES: dict[str, ObjectTypeConfig] = {
    "signals": ObjectTypeConfig(
        name="signals",
        path="signals",
        has_campaign_column=False,
        has_updated_at=False,
        allow_patch=True,
        allow_delete=True,
        fields=[
            FieldSpec("source", "text", required=True, patchable=True),
            FieldSpec("signal_type", "text", required=True, patchable=True),
            FieldSpec("payload", "jsonb", required=False, default=dict, patchable=True),
        ],
    ),
    "campaigns": ObjectTypeConfig(
        name="campaigns",
        path="campaigns",
        has_campaign_column=False,
        has_updated_at=True,
        allow_patch=True,
        allow_delete=True,
        fields=[
            FieldSpec("name", "text", required=True, patchable=True),
            FieldSpec("status", "text", required=False, default="draft", patchable=True),
            FieldSpec("starts_at", "timestamptz", required=False, patchable=True),
            FieldSpec("ends_at", "timestamptz", required=False, patchable=True),
        ],
    ),
    "opportunity_cards": ObjectTypeConfig(
        name="opportunity_cards",
        path="opportunity-cards",
        has_campaign_column=True,
        has_updated_at=True,
        allow_patch=True,
        allow_delete=True,
        fields=[
            FieldSpec("signal_id", "uuid", required=False),
            FieldSpec("title", "text", required=True, patchable=True),
            FieldSpec("score", "numeric", required=False, patchable=True),
            FieldSpec("status", "text", required=False, default="new", patchable=True),
        ],
    ),
    "briefs": ObjectTypeConfig(
        name="briefs",
        path="briefs",
        has_campaign_column=True,
        has_updated_at=True,
        allow_patch=True,
        allow_delete=True,
        fields=[
            FieldSpec("opportunity_card_id", "uuid", required=False),
            FieldSpec("title", "text", required=True, patchable=True),
            FieldSpec("body", "text", required=False, patchable=True),
        ],
    ),
    "agent_runs": ObjectTypeConfig(
        name="agent_runs",
        path="agent-runs",
        has_campaign_column=True,
        has_updated_at=False,
        allow_patch=True,
        allow_delete=True,
        fields=[
            FieldSpec("agent_name", "text", required=True),
            FieldSpec("status", "enum", required=False, default="pending", patchable=True),
            FieldSpec("input", "jsonb", required=False, default=dict),
            FieldSpec("output", "jsonb", required=False, default=dict, patchable=True),
            FieldSpec("completed_at", "timestamptz", required=False, patchable=True),
        ],
    ),
    # assets is special-cased in routers/objects.py for content_base64 /
    # content-addressed blob storage (vault/storage.py); its FieldSpec
    # list below covers everything except content handling.
    "assets": ObjectTypeConfig(
        name="assets",
        path="assets",
        has_campaign_column=True,
        has_updated_at=True,
        allow_patch=True,
        allow_delete=True,
        fields=[
            FieldSpec("brief_id", "uuid", required=False),
            FieldSpec("asset_type", "text", required=True),
            FieldSpec("agent_run_id", "uuid", required=True),
            FieldSpec("predecessor_asset_id", "uuid", required=False),
            FieldSpec("version", "integer", required=False, default=1),
            FieldSpec("approval_state", "enum", required=False, default="draft", patchable=True),
        ],
    ),
    "gate_decisions": ObjectTypeConfig(
        name="gate_decisions",
        path="gate-decisions",
        has_campaign_column=False,
        has_updated_at=False,
        allow_patch=False,
        allow_delete=False,
        fields=[
            FieldSpec("agent_run_id", "uuid", required=True),
            FieldSpec("decided_by", "text", required=True),
            FieldSpec("outcome", "enum", required=True),
            FieldSpec("reason", "text", required=False),
        ],
    ),
    "costs": ObjectTypeConfig(
        name="costs",
        path="costs",
        has_campaign_column=False,
        has_updated_at=False,
        allow_patch=False,
        allow_delete=False,
        fields=[
            FieldSpec("agent_run_id", "uuid", required=True),
            FieldSpec("provider", "text", required=True),
            FieldSpec("unit", "text", required=False, default="usd"),
            FieldSpec("amount", "numeric", required=True),
        ],
    ),
    "consent_register": ObjectTypeConfig(
        name="consent_register",
        path="consent-register",
        has_campaign_column=False,
        has_updated_at=False,
        allow_patch=True,  # revoke only — see routers/objects.py special-case
        allow_delete=False,
        fields=[
            FieldSpec("data_subject_ref", "text", required=True),
            FieldSpec("lawful_basis", "text", required=True),
            FieldSpec("channel", "text", required=True),
            FieldSpec("purpose", "text", required=True),
            FieldSpec("revoked_at", "timestamptz", required=False, patchable=True),
        ],
    ),
}


def cast_for_asyncpg(sql_type: str, value: Any) -> Any:
    """Converts a plain JSON-decoded Python value into the shape asyncpg
    expects for a given column's SQL type."""
    if value is None:
        return None
    if sql_type == "uuid":
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    if sql_type == "jsonb":
        import json

        return json.dumps(value)
    return value
