-- Canvas Marketing OS — Vault schema (v2 — contract definition only)
--
-- STATUS: this is the target DDL for TD-14's v2 contract window
-- (docs/architecture/09-technical-debt.md TD-14). It is NOT yet applied to
-- any live database and no Vault service code reads or writes through it
-- yet. This file is stage 1 of a 3-stage sequence (schema/contract
-- definitions -> consuming-service changes -> vault_internal consolidation/
-- migration, per that TD's PR plan) — the live migration, backfill, and
-- vault/ service code changes that adopt this shape are explicitly
-- out of scope for the change that introduced this file.
--
-- contracts/vault-schema/schema.sql (v1, frozen) is byte-for-byte
-- unchanged by this file and remains guarded by contracts/.frozen-v1.sha256,
-- per CLAUDE.md hard rule 1. This file is guarded separately, by its own
-- entry in contracts/.frozen-v2.sha256.
--
-- WHAT THIS CONSOLIDATES (see docs/accepted-risks.md's "Vault taxonomy/
-- consent/retention/rollup bookkeeping lives in a separate vault_internal
-- schema" risk entry, "Production hardening path"):
--
--   services/vault/migrations/0001_vault_internal_init.sql created a
--   sidecar schema, vault_internal, with 7 tables, because v1's
--   schema.sql is frozen and cannot gain columns/tables. One of those 7,
--   object_taxonomy, is a single generic table (keyed polymorphically by
--   (object_table, object_id)) carrying 5 of the 6 TaxonomyFields
--   (contracts/vault-api.yaml) for every one of the 9 Vault object types —
--   the 6th, campaign_id, already had a real column on 4 of those 9
--   tables (opportunity_cards, briefs, assets, agent_runs) and is mirrored
--   onto object_taxonomy.campaign_id for ALL 9. Every Vault read that
--   needs an object's full taxonomy (services/vault/vault/routers/
--   objects.py) does a LEFT JOIN against this sidecar table.
--
--   This file removes that LEFT JOIN by promoting all 6 TaxonomyFields to
--   real, first-class columns directly on each of the 9 object tables —
--   closing the gap between what contracts/vault-api.yaml already
--   requires on every object-create call (TaxonomyFields is a required
--   `allOf` component on every one of the 9 *Create request schemas) and
--   what the database has actually stored as a real column until now.
--   object_taxonomy itself is retired; nothing in v2 replaces it, because
--   nothing needs a polymorphic sidecar once every table has its own
--   columns.
--
--   The other 6 vault_internal tables (consent_linkage, audit_log,
--   retention_policy, retention_run, access_log, utilisation_daily) are
--   NOT joined against every object read — they are accessed through
--   their own dedicated endpoints/sweeps (services/vault/vault/consent.py,
--   audit.py, retention.py, rollup.py). A generic, polymorphic
--   (object_table, object_id) keying is the right design for audit/
--   retention/access-log bookkeeping that spans many object types — that
--   was never the freeze-driven part of the vault_internal workaround.
--   This file promotes them from an ad-hoc "sidecar" schema to a
--   first-class, versioned part of this contract (same tables, same
--   columns, same polymorphic keys — just no longer presented as a
--   workaround). See v2/spec.md for the full table-by-table mapping.
--
-- WHAT THIS DOES NOT DO: add tenant_id anywhere, or otherwise implement
-- any part of TD-05's multi-tenancy work. Per
-- docs/architecture/20-multi-tenancy-decision-memo.md §2.4/§1.6, this
-- vault-schema file needs NO v2 window for tenancy at all — schema-per-
-- tenant (the memo's recommended option) reapplies whichever schema file
-- is canonical (v1 today; this v2 file, once adopted) byte-for-byte per
-- tenant. If a future change edits this file to add a tenant_id column,
-- that is a sign that change drifted from TD-05's Option 2 back toward
-- Option 3 — stop and re-check against the memo before proceeding.
--
-- Idempotent, matching v1's convention: safe to re-run against a
-- disposable database (every statement uses IF NOT EXISTS / DO $$ guards).

BEGIN;

-- ---------------------------------------------------------------------
-- Enumerated types — the 3 carried forward unchanged from v1, plus 3 new
-- ones matching contracts/vault-api.yaml's TaxonomyFields enums exactly.
-- ---------------------------------------------------------------------

DO $$ BEGIN
    CREATE TYPE asset_approval_state AS ENUM (
        'draft', 'pending_review', 'approved', 'rejected', 'superseded'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE gate_decision_outcome AS ENUM (
        'approved', 'rejected', 'escalated'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE agent_run_status AS ENUM (
        'pending', 'running', 'succeeded', 'failed', 'cancelled'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE taxonomy_evidence_grade AS ENUM (
        'A', 'B', 'C', 'D', 'unverified'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE taxonomy_consent_status AS ENUM (
        'granted', 'revoked', 'not_required', 'pending'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE taxonomy_retention_class AS ENUM (
        'ephemeral_30d', 'standard_1y', 'extended_3y', 'legal_hold'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ---------------------------------------------------------------------
-- campaigns — top of the roll-up chain
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS campaigns (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name            text NOT NULL,
    status          text NOT NULL DEFAULT 'draft',
    starts_at       timestamptz,
    ends_at         timestamptz,
    -- Taxonomy fields promoted from vault_internal.object_taxonomy — see
    -- this file's header. Nullable: pre-v2 rows are backfilled from the
    -- real object_taxonomy row for the same (object_table, object_id)
    -- during the v2 migration (stage 3 of this window), not guessed.
    vertical        text,
    function_id    text,
    evidence_grade  taxonomy_evidence_grade,
    consent_status  taxonomy_consent_status,
    retention_class taxonomy_retention_class,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- signals — raw inbound market/customer signals
--
-- campaign_id is NEW in v2: v1 has no campaign anchor on this table at
-- all (docs/architecture/20-multi-tenancy-decision-memo.md §1.3 flags
-- signals as one of the "no anchor" tables) — the only place a signal's
-- campaign taxonomy lived was vault_internal.object_taxonomy.campaign_id.
-- Promoting it here is a direct, useful side effect for TD-05's own
-- table-by-table tenancy bucketing, not something this window had to do,
-- but worth noting for whoever picks up TD-05 Part 2 next.
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS signals (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source          text NOT NULL,
    signal_type     text NOT NULL,
    payload         jsonb NOT NULL DEFAULT '{}'::jsonb,
    campaign_id     uuid REFERENCES campaigns(id),
    vertical        text,
    function_id    text,
    evidence_grade  taxonomy_evidence_grade,
    consent_status  taxonomy_consent_status,
    retention_class taxonomy_retention_class,
    received_at     timestamptz NOT NULL DEFAULT now(),
    created_at      timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- opportunity_cards — scored opportunities derived from signals
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS opportunity_cards (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    signal_id       uuid REFERENCES signals(id),
    campaign_id     uuid REFERENCES campaigns(id),
    title           text NOT NULL,
    score           numeric,
    status          text NOT NULL DEFAULT 'new',
    pillar          text,
    so_what         text,
    source_url      text,
    confidence      text,
    vertical        text,
    function_id    text,
    evidence_grade  taxonomy_evidence_grade,
    consent_status  taxonomy_consent_status,
    retention_class taxonomy_retention_class,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- briefs — creative/marketing briefs derived from opportunity cards
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS briefs (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    opportunity_card_id uuid REFERENCES opportunity_cards(id),
    campaign_id         uuid REFERENCES campaigns(id),
    title               text NOT NULL,
    body                text,
    vertical        text,
    function_id    text,
    evidence_grade  taxonomy_evidence_grade,
    consent_status  taxonomy_consent_status,
    retention_class taxonomy_retention_class,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- agent_runs — every agent/tool invocation; FK to campaigns for roll-up
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS agent_runs (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    campaign_id     uuid NOT NULL REFERENCES campaigns(id),
    agent_name      text NOT NULL,
    status          agent_run_status NOT NULL DEFAULT 'pending',
    input           jsonb,
    output          jsonb,
    vertical        text,
    function_id    text,
    evidence_grade  taxonomy_evidence_grade,
    consent_status  taxonomy_consent_status,
    retention_class taxonomy_retention_class,
    started_at      timestamptz NOT NULL DEFAULT now(),
    completed_at    timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- assets — version + approval chain
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS assets (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    brief_id            uuid REFERENCES briefs(id),
    campaign_id         uuid REFERENCES campaigns(id),
    asset_type          text NOT NULL,
    version             integer NOT NULL DEFAULT 1,
    approval_state      asset_approval_state NOT NULL DEFAULT 'draft',
    agent_run_id        uuid NOT NULL REFERENCES agent_runs(id),
    predecessor_asset_id uuid REFERENCES assets(id),
    storage_uri         text,
    content_hash        text,
    vertical        text,
    function_id    text,
    evidence_grade  taxonomy_evidence_grade,
    consent_status  taxonomy_consent_status,
    retention_class taxonomy_retention_class,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT assets_predecessor_not_self CHECK (predecessor_asset_id IS DISTINCT FROM id)
);

-- ---------------------------------------------------------------------
-- gate_decisions — append-only; not-null FK to agent_runs
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS gate_decisions (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_run_id    uuid NOT NULL REFERENCES agent_runs(id),
    decided_by      text NOT NULL,
    outcome         gate_decision_outcome NOT NULL,
    reason          text,
    vertical        text,
    function_id    text,
    evidence_grade  taxonomy_evidence_grade,
    consent_status  taxonomy_consent_status,
    retention_class taxonomy_retention_class,
    decided_at      timestamptz NOT NULL DEFAULT now(),
    created_at      timestamptz NOT NULL DEFAULT now()
    -- Append-only by convention, unchanged from v1: re-decisions insert a
    -- new row referencing the same agent_run_id rather than UPDATEing a
    -- prior decision. No updated_at column, deliberately.
);

-- ---------------------------------------------------------------------
-- costs — FK to agent_runs; agent_runs -> campaigns completes the
-- unbroken roll-up chain (costs -> agent_runs -> campaigns).
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS costs (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_run_id    uuid NOT NULL REFERENCES agent_runs(id),
    provider        text NOT NULL,
    unit            text NOT NULL DEFAULT 'usd',
    amount          numeric(18, 6) NOT NULL,
    vertical        text,
    function_id    text,
    evidence_grade  taxonomy_evidence_grade,
    consent_status  taxonomy_consent_status,
    retention_class taxonomy_retention_class,
    incurred_at     timestamptz NOT NULL DEFAULT now(),
    created_at      timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- consent_register — POPIA s11/s69-shaped consent records
--
-- campaign_id is NEW in v2, same rationale as signals above — this table
-- had no campaign anchor in v1 (TD-05 memo §1.3's other "no anchor" hit).
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS consent_register (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    data_subject_ref text NOT NULL,
    lawful_basis    text NOT NULL,
    channel         text NOT NULL,
    purpose         text NOT NULL,
    campaign_id     uuid REFERENCES campaigns(id),
    vertical        text,
    function_id    text,
    evidence_grade  taxonomy_evidence_grade,
    consent_status  taxonomy_consent_status,
    retention_class taxonomy_retention_class,
    consented_at    timestamptz NOT NULL DEFAULT now(),
    revoked_at      timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT consent_register_unique_grant UNIQUE (data_subject_ref, channel, purpose, consented_at)
);

-- ---------------------------------------------------------------------
-- consent_linkage — durably links a client-derived object to the exact
-- consent_register row that authorized it. Carried forward from
-- vault_internal unchanged: a polymorphic (object_table, object_id) key
-- is the right shape here (any of the 9 object types may be
-- client-derived), so this is promoted to first-class, not inlined.
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS consent_linkage (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    object_table        text NOT NULL,
    object_id           uuid NOT NULL,
    consent_register_id uuid NOT NULL REFERENCES consent_register(id),
    decided_at          timestamptz NOT NULL DEFAULT now(),
    created_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT consent_linkage_unique_object UNIQUE (object_table, object_id)
);

-- ---------------------------------------------------------------------
-- audit_log — single shared table for every audit-emitting code path.
-- Carried forward from vault_internal unchanged.
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS audit_log (
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

-- ---------------------------------------------------------------------
-- retention_policy — retention_class -> expires_at bookkeeping, keyed by
-- (object_table, object_id), covering all object types. Carried forward
-- from vault_internal unchanged. NOTE: this table's own retention_class
-- column is the operational sweep driver (when does this row expire);
-- the retention_class column now also promoted onto each object table
-- above is that same object's own taxonomy value — the two are meant to
-- agree, and the v2 migration (stage 3) must populate both from the same
-- source, but they are deliberately two separate columns: this table
-- tracks expires_at/deleted_at sweep state that has no equivalent on the
-- object tables themselves.
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS retention_policy (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    object_table    text NOT NULL,
    object_id       uuid NOT NULL,
    retention_class text NOT NULL,
    expires_at      timestamptz NOT NULL,
    deleted_at      timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT retention_policy_unique_object UNIQUE (object_table, object_id)
);

-- ---------------------------------------------------------------------
-- retention_run — bookkeeping for POST/GET /retention-expiry-runs.
-- Carried forward from vault_internal unchanged.
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS retention_run (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    status          text NOT NULL DEFAULT 'running',
    started_at      timestamptz NOT NULL DEFAULT now(),
    completed_at    timestamptz,
    deleted_count   integer NOT NULL DEFAULT 0,
    detail          jsonb NOT NULL DEFAULT '{}'::jsonb
);

-- ---------------------------------------------------------------------
-- access_log — one row per GET on an object resource. Carried forward
-- from vault_internal unchanged.
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS access_log (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    object_table    text NOT NULL,
    object_id       uuid,
    caller_service  text NOT NULL,
    occurred_at     timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- utilisation_daily — real daily rollup TABLE, one row per (day,
-- object_table, caller_service). Carried forward from vault_internal
-- unchanged.
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS utilisation_daily (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    day             date NOT NULL,
    object_table    text NOT NULL,
    caller_service  text NOT NULL,
    read_count      integer NOT NULL DEFAULT 0,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT utilisation_daily_unique_row UNIQUE (day, object_table, caller_service)
);

-- ---------------------------------------------------------------------
-- Indexes
-- ---------------------------------------------------------------------

CREATE INDEX IF NOT EXISTS idx_signals_campaign_id ON signals(campaign_id);
CREATE INDEX IF NOT EXISTS idx_opportunity_cards_signal_id ON opportunity_cards(signal_id);
CREATE INDEX IF NOT EXISTS idx_opportunity_cards_campaign_id ON opportunity_cards(campaign_id);
CREATE INDEX IF NOT EXISTS idx_briefs_opportunity_card_id ON briefs(opportunity_card_id);
CREATE INDEX IF NOT EXISTS idx_briefs_campaign_id ON briefs(campaign_id);
CREATE INDEX IF NOT EXISTS idx_agent_runs_campaign_id ON agent_runs(campaign_id);
CREATE INDEX IF NOT EXISTS idx_assets_brief_id ON assets(brief_id);
CREATE INDEX IF NOT EXISTS idx_assets_campaign_id ON assets(campaign_id);
CREATE INDEX IF NOT EXISTS idx_assets_agent_run_id ON assets(agent_run_id);
CREATE INDEX IF NOT EXISTS idx_assets_predecessor_asset_id ON assets(predecessor_asset_id);
CREATE INDEX IF NOT EXISTS idx_gate_decisions_agent_run_id ON gate_decisions(agent_run_id);
CREATE INDEX IF NOT EXISTS idx_costs_agent_run_id ON costs(agent_run_id);
CREATE INDEX IF NOT EXISTS idx_consent_register_data_subject_ref ON consent_register(data_subject_ref);
CREATE INDEX IF NOT EXISTS idx_consent_register_campaign_id ON consent_register(campaign_id);
CREATE INDEX IF NOT EXISTS idx_consent_linkage_object ON consent_linkage(object_table, object_id);
CREATE INDEX IF NOT EXISTS idx_consent_linkage_consent_register_id ON consent_linkage(consent_register_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_event_type ON audit_log(event_type);
CREATE INDEX IF NOT EXISTS idx_audit_log_object ON audit_log(object_table, object_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_correlation_id ON audit_log(correlation_id);
CREATE INDEX IF NOT EXISTS idx_retention_policy_expires_at ON retention_policy(expires_at) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_access_log_occurred_at ON access_log(occurred_at);
CREATE INDEX IF NOT EXISTS idx_access_log_object_table ON access_log(object_table);
CREATE INDEX IF NOT EXISTS idx_utilisation_daily_day ON utilisation_daily(day);

COMMIT;
