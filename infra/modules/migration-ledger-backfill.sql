-- Canvas Marketing OS — one-time migration-ledger backfill (TD-15)
--
-- NOT wired into deploy-infra, any GitHub workflow, or any Container Apps
-- Job. This is a standalone, human-run, ONE-TIME operational script — run
-- it by hand against the live cae-cmos-dev Postgres, once, at or before
-- the first deploy-infra run after the TD-15 migration-ledger change
-- merges to main.
--
-- WHY THIS EXISTS. Every *-migration-job.bicep now uses
-- infra/modules/migration-ledger-runner.sh, which skips any migration
-- version already recorded in that job's ledger table. On a database
-- that has never had a ledger table, the runner's first-ever invocation
-- treats every version as unrecorded and (re-)applies all of them. For
-- migrations that are pure CREATE/ALTER ... IF NOT EXISTS, that one extra
-- pass is a harmless no-op. For services/orchestrator/migrations/
-- 0003_qa_blocked_reason.sql and 0004_dependency_dead_lettered_reason.sql
-- specifically, it is not quite free: their DROP + ADD CONSTRAINT ...
-- NOT VALID takes a brief ACCESS EXCLUSIVE lock on task_transitions even
-- though NOT VALID skips the historical row-scan that caused TD-15's
-- actual outage — a small but real, and now entirely avoidable, cost
-- against a live table with production traffic on it.
--
-- Running this script first means the transition deploy's ledger-gated
-- run sees every migration below as already applied and does nothing to
-- any of these tables at all — the exact "never re-run once recorded"
-- guarantee this whole change exists to provide, extended to cover the
-- transition itself.
--
-- SAFE TO RUN MULTIPLE TIMES (every INSERT is ON CONFLICT DO NOTHING).
-- NOT SAFE TO RUN AGAINST AN ENVIRONMENT WHERE THESE MIGRATIONS HAVE
-- NOT ACTUALLY BEEN APPLIED — e.g. a fresh dev/CI database. Running it
-- there would mark versions "applied" without their DDL ever having run,
-- silently corrupting that environment's schema. Only run this against
-- cae-cmos-dev's real Postgres server, and only after confirming (e.g.
-- via a live psql session) that the tables/columns/constraints these
-- versions create already exist.
--
-- Usage:
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f infra/modules/migration-ledger-backfill.sql
--
-- Sections below are independent — comment out any section for a service
-- whose migrations you have not independently confirmed are already live.

BEGIN;

-- ---------------------------------------------------------------------
-- orchestrator (public.orchestrator_schema_migrations) — RECOMMENDED.
-- All 5 files were, as of this change, already live via the old
-- unconditional-join mechanism (caj-orchestrator-migrate runs on every
-- deploy-infra). Backfilling avoids one further redundant DROP + ADD
-- CONSTRAINT pass over task_transitions (see header above).
-- ---------------------------------------------------------------------

CREATE SCHEMA IF NOT EXISTS public;
CREATE TABLE IF NOT EXISTS public.orchestrator_schema_migrations (
    version     text PRIMARY KEY,
    applied_at  timestamptz NOT NULL DEFAULT now()
);
INSERT INTO public.orchestrator_schema_migrations (version) VALUES
    ('0001_orchestrator_init'),
    ('0002_task_result_ref'),
    ('0003_qa_blocked_reason'),
    ('0004_dependency_dead_lettered_reason'),
    ('0005_agent_run_idempotency')
ON CONFLICT (version) DO NOTHING;

-- ---------------------------------------------------------------------
-- governance (governance.schema_migrations) — OPTIONAL.
-- Both files are pure CREATE SCHEMA/TABLE IF NOT EXISTS + ADD COLUMN IF
-- NOT EXISTS; a redundant re-run costs nothing beyond a trivial catalog
-- lookup. Backfilling here only saves that lookup, not a real risk.
-- governance.schema_migrations already exists (0001_governance_init.sql's
-- own INSERT) — these rows are almost certainly already present; the
-- ON CONFLICT DO NOTHING makes this a safe no-op either way.
-- ---------------------------------------------------------------------

CREATE SCHEMA IF NOT EXISTS governance;
CREATE TABLE IF NOT EXISTS governance.schema_migrations (
    version     text PRIMARY KEY,
    applied_at  timestamptz NOT NULL DEFAULT now()
);
INSERT INTO governance.schema_migrations (version) VALUES
    ('0001_governance_init'),
    ('0002_kill_switch_decided_by')
ON CONFLICT (version) DO NOTHING;

-- ---------------------------------------------------------------------
-- vault options-inbox (public.vault_options_inbox_schema_migrations) —
-- OPTIONAL. Both files are pure CREATE TABLE/ADD COLUMN IF NOT EXISTS;
-- a redundant re-run costs nothing beyond a trivial catalog lookup.
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS public.vault_options_inbox_schema_migrations (
    version     text PRIMARY KEY,
    applied_at  timestamptz NOT NULL DEFAULT now()
);
INSERT INTO public.vault_options_inbox_schema_migrations (version) VALUES
    ('0002_options_inbox_init'),
    ('0003_approval_decisions_add_channel')
ON CONFLICT (version) DO NOTHING;

-- ---------------------------------------------------------------------
-- vault sidecar (vault_internal.schema_migrations) — OPTIONAL, one file.
-- ---------------------------------------------------------------------

CREATE SCHEMA IF NOT EXISTS vault_internal;
CREATE TABLE IF NOT EXISTS vault_internal.schema_migrations (
    version     text PRIMARY KEY,
    applied_at  timestamptz NOT NULL DEFAULT now()
);
INSERT INTO vault_internal.schema_migrations (version) VALUES
    ('0001_vault_internal_init')
ON CONFLICT (version) DO NOTHING;

-- ---------------------------------------------------------------------
-- model-gateway (public.gateway_schema_migrations) — OPTIONAL, one file.
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS public.gateway_schema_migrations (
    version     text PRIMARY KEY,
    applied_at  timestamptz NOT NULL DEFAULT now()
);
INSERT INTO public.gateway_schema_migrations (version) VALUES
    ('0001_completions_init')
ON CONFLICT (version) DO NOTHING;

-- ---------------------------------------------------------------------
-- analytics-ingest (analytics.schema_migrations) — OPTIONAL, one file.
-- ---------------------------------------------------------------------

CREATE SCHEMA IF NOT EXISTS analytics;
CREATE TABLE IF NOT EXISTS analytics.schema_migrations (
    version     text PRIMARY KEY,
    applied_at  timestamptz NOT NULL DEFAULT now()
);
INSERT INTO analytics.schema_migrations (version) VALUES
    ('0001_analytics_init')
ON CONFLICT (version) DO NOTHING;

COMMIT;
