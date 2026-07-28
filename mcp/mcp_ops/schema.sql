-- Canvas Marketing OS — mcp_ops schema (tool-call logging)
--
-- Additive-only, session-owned Postgres schema for the MCP tool plane's
-- tool-call logging (AC-8/AC-9). Lives entirely in its own `mcp_ops`
-- schema, separate from `public` (where contracts/vault-schema/schema.sql
-- lives) — this file never touches contracts/vault-schema/schema.sql or
-- any of its tables, and has no FK dependency on them (tool-call logging
-- is a standalone concern, not campaign-scoped).
--
-- Applied by infra/modules/mcp/mcp-ops-migrate-job.bicep (caj-mcp-ops-
-- migrate), which reuses migration-job.bicep's exact one-shot Container
-- Apps Job mechanism (Manual trigger, replicaRetryLimit 1, base64-encoded
-- SQL secret to survive Container Apps' "$$" -> "$" collapse) per ruling
-- R3 — not a forked mechanism.
--
-- Idempotent: safe to re-run against a disposable/shared database, same
-- BEGIN/COMMIT + DO $$ ... EXCEPTION WHEN duplicate_object pattern as
-- contracts/vault-schema/schema.sql.

BEGIN;

CREATE SCHEMA IF NOT EXISTS mcp_ops;

-- ---------------------------------------------------------------------
-- Enumerated types
-- ---------------------------------------------------------------------

DO $$ BEGIN
    CREATE TYPE mcp_ops.tool_call_outcome AS ENUM (
        'success', 'error', 'rejected', 'rate_limited'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ---------------------------------------------------------------------
-- mcp_ops.tool_calls — one row per real tool call made through a
-- server's logging code path. Never stores raw arguments — only a
-- fixed-length SHA-256 hex digest of their canonical-JSON form, so no
-- payload/PII data reaches this log table (POPIA-safe by construction).
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS mcp_ops.tool_calls (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    server_name     text NOT NULL,
    tool_name       text NOT NULL,
    caller_identity text NOT NULL,
    arguments_hash  text NOT NULL,
    latency_ms      numeric NOT NULL,
    outcome         mcp_ops.tool_call_outcome NOT NULL,
    called_at       timestamptz NOT NULL DEFAULT now(),
    created_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT tool_calls_arguments_hash_length CHECK (char_length(arguments_hash) = 64),
    CONSTRAINT tool_calls_latency_ms_non_negative CHECK (latency_ms >= 0)
);

CREATE INDEX IF NOT EXISTS idx_mcp_ops_tool_calls_called_at ON mcp_ops.tool_calls(called_at);
CREATE INDEX IF NOT EXISTS idx_mcp_ops_tool_calls_server_tool ON mcp_ops.tool_calls(server_name, tool_name);

COMMIT;
