-- Canvas Marketing OS — Vault service sidecar schema (vault_internal)
--
-- This migration creates a NEW schema, `vault_internal`, alongside the
-- frozen `public` schema defined by contracts/vault-schema/schema.sql.
-- It NEVER touches the 9 frozen public-schema tables or their columns —
-- contracts/.frozen-v1.sha256 keeps guarding that file unmodified. This
-- migration is purely additive: taxonomy, consent-linkage, retention, and
-- utilisation-rollup bookkeeping for the Vault service lives here instead
-- (see .loop/spec.json OQ-1-RESOLVED and docs/accepted-risks.md for the
-- rationale and the deferred v2-consolidation decision).
--
-- Idempotent: every statement uses IF NOT EXISTS guards (CREATE SCHEMA
-- IF NOT EXISTS, CREATE TABLE IF NOT EXISTS, CREATE INDEX IF NOT EXISTS)
-- so this file can be safely re-applied against an already-migrated
-- database (verified in CI by applying it twice in a row). This file
-- contains no "DO $$ ... $$" PL/pgSQL blocks — plain idempotent DDL is
-- sufficient here.
--
-- Applied through the same in-VNet Container Apps Job mechanism as
-- caj-vault-migrate (infra/modules/vault/sidecar-migration-job.bicep,
-- caj-vault-sidecar-migrate) — including the identical base64-encoding
-- fix for Container Apps' handling of "$$" in secret values, applied
-- defensively regardless of whether a given migration file happens to
-- use dollar-quoting (see infra/modules/migration-job.bicep's comment
-- header for the original fix this mirrors).

BEGIN;

CREATE SCHEMA IF NOT EXISTS vault_internal;

-- ---------------------------------------------------------------------
-- object_taxonomy — the 6 mandatory taxonomy fields, for every Vault
-- object of every type. `campaign_id` is additionally mirrored onto the
-- real `campaign_id` column for opportunity_cards/briefs/assets/
-- agent_runs (the 4 tables with an actual public-schema home for it —
-- see AC-003); object_taxonomy is the uniform canonical store for all 9
-- object types regardless of whether a real column exists.
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS vault_internal.object_taxonomy (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    object_table    text NOT NULL,
    object_id       uuid NOT NULL,
    vertical        text NOT NULL,
    function_id     text NOT NULL,
    campaign_id     uuid REFERENCES public.campaigns(id),
    evidence_grade  text NOT NULL,
    consent_status  text NOT NULL,
    retention_class text NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT object_taxonomy_unique_object UNIQUE (object_table, object_id)
);

CREATE INDEX IF NOT EXISTS idx_object_taxonomy_object
    ON vault_internal.object_taxonomy(object_table, object_id);
CREATE INDEX IF NOT EXISTS idx_object_taxonomy_campaign_id
    ON vault_internal.object_taxonomy(campaign_id);

-- ---------------------------------------------------------------------
-- consent_linkage — durably links a client-derived object to the exact
-- consent_register row that authorized it (AC-004).
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS vault_internal.consent_linkage (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    object_table        text NOT NULL,
    object_id           uuid NOT NULL,
    consent_register_id uuid NOT NULL REFERENCES public.consent_register(id),
    decided_at          timestamptz NOT NULL DEFAULT now(),
    created_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT consent_linkage_unique_object UNIQUE (object_table, object_id)
);

CREATE INDEX IF NOT EXISTS idx_consent_linkage_object
    ON vault_internal.consent_linkage(object_table, object_id);
CREATE INDEX IF NOT EXISTS idx_consent_linkage_consent_register_id
    ON vault_internal.consent_linkage(consent_register_id);

-- ---------------------------------------------------------------------
-- audit_log — single shared table for all 3 audit-emitting code paths
-- (taxonomy rejection, consent rejection, retention deletion — AC-016).
-- One consistent column set across all event_types by construction; see
-- services/vault/vault/audit.py's single write_audit() helper.
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS vault_internal.audit_log (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    correlation_id  uuid NOT NULL DEFAULT gen_random_uuid(),
    event_type      text NOT NULL,
    object_table    text,
    object_id       uuid,
    data_subject_ref text,
    reason          text,
    actor           text,
    detail          jsonb NOT NULL DEFAULT '{}'::jsonb,
    occurred_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_audit_log_event_type ON vault_internal.audit_log(event_type);
CREATE INDEX IF NOT EXISTS idx_audit_log_object ON vault_internal.audit_log(object_table, object_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_correlation_id ON vault_internal.audit_log(correlation_id);

-- ---------------------------------------------------------------------
-- retention_policy — retention_class -> expires_at bookkeeping, keyed by
-- (object_table, object_id), covering ALL object types, not just assets
-- (AC-007).
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS vault_internal.retention_policy (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    object_table    text NOT NULL,
    object_id       uuid NOT NULL,
    retention_class text NOT NULL,
    expires_at      timestamptz NOT NULL,
    deleted_at      timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT retention_policy_unique_object UNIQUE (object_table, object_id)
);

CREATE INDEX IF NOT EXISTS idx_retention_policy_expires_at
    ON vault_internal.retention_policy(expires_at) WHERE deleted_at IS NULL;

-- ---------------------------------------------------------------------
-- retention_run — bookkeeping for POST/GET /retention-expiry-runs.
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS vault_internal.retention_run (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    status          text NOT NULL DEFAULT 'running',
    started_at      timestamptz NOT NULL DEFAULT now(),
    completed_at    timestamptz,
    deleted_count   integer NOT NULL DEFAULT 0,
    detail          jsonb NOT NULL DEFAULT '{}'::jsonb
);

-- ---------------------------------------------------------------------
-- access_log — one row per GET on an object resource, keyed by the
-- calling service (X-Caller-Service header), feeding utilisation_daily.
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS vault_internal.access_log (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    object_table    text NOT NULL,
    object_id       uuid,
    caller_service  text NOT NULL,
    occurred_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_access_log_occurred_at ON vault_internal.access_log(occurred_at);
CREATE INDEX IF NOT EXISTS idx_access_log_object_table ON vault_internal.access_log(object_table);

-- ---------------------------------------------------------------------
-- utilisation_daily — real daily rollup TABLE (AC-008), one row per
-- (day, object_table, caller_service).
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS vault_internal.utilisation_daily (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    day             date NOT NULL,
    object_table    text NOT NULL,
    caller_service  text NOT NULL,
    read_count      integer NOT NULL DEFAULT 0,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT utilisation_daily_unique_row UNIQUE (day, object_table, caller_service)
);

CREATE INDEX IF NOT EXISTS idx_utilisation_daily_day ON vault_internal.utilisation_daily(day);

COMMIT;
