-- Canvas Marketing OS — analytics-ingest schema migration
--
-- This migration creates a NEW schema, `analytics`, alongside (and never
-- touching) the frozen `public` schema defined by
-- contracts/vault-schema/schema.sql — analytics-ingest owns this schema
-- entirely and reads Vault exclusively via its REST API (never a direct
-- Postgres connection to the Vault's own database), per C-VAULT-TABLES.
--
-- Idempotent: every statement uses IF NOT EXISTS guards (CREATE SCHEMA IF
-- NOT EXISTS, CREATE TABLE IF NOT EXISTS, CREATE INDEX IF NOT EXISTS) so
-- this file can be safely re-applied against an already-migrated database
-- (verified by applying it twice in a row). This file contains no
-- "DO $$ ... $$" PL/pgSQL blocks — plain idempotent DDL is sufficient
-- here, mirroring services/vault/migrations/0001_vault_internal_init.sql's
-- style exactly.
--
-- Applied through the same in-VNet Container Apps Job mechanism as
-- caj-vault-migrate/caj-vault-sidecar-migrate
-- (infra/modules/analytics/migration-job.bicep, caj-analytics-migrate) —
-- including the identical base64-encoding fix for Container Apps' handling
-- of "$$" in secret values, applied defensively regardless of whether this
-- file happens to use dollar-quoting (see infra/modules/migration-job.bicep's
-- comment header for the original fix this mirrors).
--
-- Idempotency convention (C-IDEMPOTENCY):
--   * raw fact tables: UNIQUE(source, day, natural_row_id) + INSERT ...
--     ON CONFLICT DO NOTHING (never clobbers a hand-corrected quarantine
--     row or re-ingested row).
--   * KPI rollup tables: UNIQUE(day, kpi_name, <dimension...>) + INSERT
--     ... ON CONFLICT DO UPDATE (rollups refresh cleanly on re-run).

BEGIN;

CREATE SCHEMA IF NOT EXISTS analytics;

-- ---------------------------------------------------------------------
-- Raw fact tables — one row per (source, day, natural_row_id).
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS analytics.buffer_post_metrics (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source          text NOT NULL DEFAULT 'buffer',
    day             date NOT NULL,
    natural_row_id  text NOT NULL,
    post_id         text,
    utm_campaign    text,
    impressions     integer,
    reactions       integer,
    comments        integer,
    shares          integer,
    clicks          integer,
    engagement_rate numeric(10,6),
    post_archetype  text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT buffer_post_metrics_unique_row UNIQUE (source, day, natural_row_id)
);

CREATE TABLE IF NOT EXISTS analytics.ga4_metrics (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source          text NOT NULL DEFAULT 'ga4',
    day             date NOT NULL,
    natural_row_id  text NOT NULL,
    landing_page    text,
    sessions        integer,
    engaged_sessions integer,
    conversions     integer,
    utm_campaign    text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ga4_metrics_unique_row UNIQUE (source, day, natural_row_id)
);

CREATE TABLE IF NOT EXISTS analytics.search_console_metrics (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source          text NOT NULL DEFAULT 'search_console',
    day             date NOT NULL,
    natural_row_id  text NOT NULL,
    query           text,
    page            text,
    clicks          integer,
    impressions     integer,
    ctr             numeric(10,6),
    position        numeric(10,2),
    created_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT search_console_metrics_unique_row UNIQUE (source, day, natural_row_id)
);

CREATE TABLE IF NOT EXISTS analytics.linkedin_metrics (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source          text NOT NULL DEFAULT 'linkedin',
    day             date NOT NULL,
    natural_row_id  text NOT NULL,
    post_id         text,
    utm_campaign    text,
    impressions     integer,
    reactions       integer,
    comments        integer,
    shares          integer,
    clicks          integer,
    engagement_rate numeric(10,6),
    post_archetype  text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT linkedin_metrics_unique_row UNIQUE (source, day, natural_row_id)
);

-- ---------------------------------------------------------------------
-- UTM reconciliation — the sole mapping/quarantine mechanism (C-UTM).
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS analytics.utm_campaign_map (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    utm_campaign_slug   text NOT NULL,
    vault_campaign_id   uuid,
    asset_id            uuid,
    created_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT utm_campaign_map_unique_slug UNIQUE (utm_campaign_slug)
);

CREATE TABLE IF NOT EXISTS analytics.utm_quarantine (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source          text NOT NULL,
    day             date NOT NULL,
    natural_row_id  text NOT NULL,
    utm_campaign_raw text,
    reason          text NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT utm_quarantine_unique_row UNIQUE (source, day, natural_row_id)
);

-- ---------------------------------------------------------------------
-- scheduled_posts — small seed table for the publishing-reliability KPI's
-- denominator (how many posts were scheduled for a channel/day).
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS analytics.scheduled_posts (
    day             date NOT NULL,
    channel         text NOT NULL,
    scheduled_count integer NOT NULL,
    CONSTRAINT scheduled_posts_unique_row UNIQUE (day, channel)
);

-- ---------------------------------------------------------------------
-- KPI rollup tables — one row per (day, kpi_name, <dimension...>),
-- refreshed on every nightly re-run via ON CONFLICT DO UPDATE.
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS analytics.kpi_rollup_engagement_by_archetype (
    day             date NOT NULL,
    kpi_name        text NOT NULL DEFAULT 'engagement_rate_by_post_archetype',
    source          text NOT NULL,
    post_archetype  text NOT NULL,
    engagement_rate numeric(10,6) NOT NULL,
    post_count      integer NOT NULL,
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT kpi_rollup_engagement_by_archetype_unique_row
        UNIQUE (day, kpi_name, source, post_archetype)
);

CREATE TABLE IF NOT EXISTS analytics.kpi_rollup_publishing_reliability (
    day                 date NOT NULL,
    kpi_name            text NOT NULL DEFAULT 'publishing_reliability',
    channel             text NOT NULL,
    scheduled_count     integer NOT NULL,
    published_count     integer NOT NULL,
    reliability_rate    numeric(10,6) NOT NULL,
    updated_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT kpi_rollup_publishing_reliability_unique_row
        UNIQUE (day, kpi_name, channel)
);

CREATE TABLE IF NOT EXISTS analytics.kpi_rollup_cost_per_accepted_asset (
    day                     date NOT NULL,
    kpi_name                text NOT NULL DEFAULT 'cost_per_accepted_asset',
    agent_name              text NOT NULL,
    total_cost_usd          numeric(12,4) NOT NULL,
    accepted_asset_count    integer NOT NULL,
    cost_per_accepted_asset numeric(12,4) NOT NULL,
    updated_at              timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT kpi_rollup_cost_per_accepted_asset_unique_row
        UNIQUE (day, kpi_name, agent_name)
);

CREATE TABLE IF NOT EXISTS analytics.kpi_rollup_vault_utilisation (
    day             date NOT NULL,
    kpi_name        text NOT NULL DEFAULT 'vault_utilisation',
    object_class    text NOT NULL,
    caller_service  text NOT NULL,
    read_count      integer NOT NULL,
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT kpi_rollup_vault_utilisation_unique_row
        UNIQUE (day, kpi_name, object_class, caller_service)
);

COMMIT;
