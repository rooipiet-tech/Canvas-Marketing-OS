-- Canvas Marketing OS — model-gateway service schema (0001_completions_init)
--
-- TD-06 fix: caching.py's task_ref idempotency guarantee used to be a pair
-- of module-level Python dicts, explicitly scoped "process-local ... out of
-- scope" for multi-replica consistency. ca-model-gateway autoscales, so two
-- replicas racing on the same task_ref produced two upstream provider calls
-- and two costs rows — the double-billing this table exists to close.
--
-- Additive `public`-schema table, own migration, own Container Apps Job
-- (infra/modules/gateway-migration-job.bicep, caj-gateway-migrate) — this
-- never touches contracts/vault-schema/schema.sql, which stays frozen at
-- v1 (CLAUDE.md hard rule 1). Same convention as
-- services/orchestrator/migrations/0001_orchestrator_init.sql and
-- services/analytics-ingest/migrations/0001_analytics_init.sql: an
-- additive table the owning service migrates itself, never a change to the
-- frozen Vault schema.
--
-- Idempotent: IF NOT EXISTS guards throughout, safe to re-apply.
--
-- One row per task_ref, written exactly once by whichever ca-model-gateway
-- replica wins the session-scoped Postgres advisory lock in caching.py's
-- PostgresCache (see that module's header for the full locking protocol).
-- `response` is the full CompletionResponse body a retried request for the
-- same task_ref replays verbatim — no column here stores raw prompt/
-- completion *input*, only the already-redaction-cleared response the
-- caller already received once.

BEGIN;

CREATE TABLE IF NOT EXISTS completions (
    task_ref    text PRIMARY KEY,
    response    jsonb NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

COMMIT;
