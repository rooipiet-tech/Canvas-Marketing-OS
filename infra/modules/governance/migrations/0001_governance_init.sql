-- Canvas Marketing OS — governance schema migration 0001
--
-- WHY A SEPARATE SCHEMA (not new tables in `public`):
--   contracts/vault-schema/schema.sql is a FROZEN v1 contract, hash-pinned
--   by scripts/validate_contracts.py, and ci.yml's migration-test job
--   asserts that `public` contains exactly the nine core Vault tables.
--   S4's governance state (kill switches, approval inbox, approval-action
--   audit, publish attempts, jti replay ledger) is additive and therefore
--   lives in its own `governance` schema. The frozen nine-table assertion
--   over `public` keeps passing untouched.
--
-- WHY NO NEW ENUM TYPES:
--   CREATE TYPE is not idempotent without a DO $$ ... $$ block, and
--   PL/pgSQL dollar-quoting is exactly what Container Apps' "$$" -> "$"
--   secret-value collapse corrupts (see L-0012 / migration-job.bicep).
--   This file deliberately contains ZERO dollar signs: constrained
--   domains are expressed as CHECK constraints over plain `text` columns,
--   so the base64 of this file is safe to hand to a Container Apps Job
--   secret verbatim.
--
-- Idempotent: every object is created IF NOT EXISTS, so the migration job
-- can be re-run any number of times with zero errors.

BEGIN;

CREATE SCHEMA IF NOT EXISTS governance;

-- ---------------------------------------------------------------------
-- schema_migrations — which governance migrations have been applied
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS governance.schema_migrations (
    version         text PRIMARY KEY,
    applied_at      timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- kill_switches — Postgres-backed governance kill switches.
--
-- scope='global'   -> function_id MUST be NULL; blocks every function.
-- scope='function' -> function_id MUST be set; blocks only that function.
--
-- Read directly (uncached) on every gate decision and every publish
-- attempt, so flipping `active` propagates in well under the 5s bound.
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS governance.kill_switches (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope           text NOT NULL,
    function_id     text,
    active          boolean NOT NULL DEFAULT true,
    reason          text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT kill_switches_scope_allowed
        CHECK (scope IN ('global', 'function')),
    CONSTRAINT kill_switches_scope_function_id_consistent
        CHECK (
            (scope = 'global' AND function_id IS NULL)
            OR (scope = 'function' AND function_id IS NOT NULL)
        )
);

-- ---------------------------------------------------------------------
-- approval_inbox — Gatekeeper-owned approval queue.
--
-- Written on every approval-required (level 1/2) decision, whether or not
-- a Teams webhook is configured: the row is what makes the Approve/Reject
-- deep link single-use and time-bounded. `link_token` is generated with
-- secrets.token_urlsafe (URL-safe alphabet only — see L-0004).
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS governance.approval_inbox (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    gate_decision_id    uuid,
    agent_run_id        uuid NOT NULL,
    function_id         text NOT NULL,
    action_class        text NOT NULL,
    level               integer NOT NULL,
    content_hash        text,
    preview_title       text NOT NULL,
    preview_reference   text,
    evidence_summary    text NOT NULL,
    status              text NOT NULL DEFAULT 'pending',
    link_token          text NOT NULL,
    link_consumed_at    timestamptz,
    decided_by          text,
    decided_at          timestamptz,
    expires_at          timestamptz NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT approval_inbox_link_token_unique UNIQUE (link_token),
    CONSTRAINT approval_inbox_level_range CHECK (level >= 0 AND level <= 4),
    CONSTRAINT approval_inbox_status_allowed
        CHECK (status IN ('pending', 'approved', 'rejected', 'expired'))
);

-- ---------------------------------------------------------------------
-- approval_actions — audit of every click on the approval-action
-- endpoint. Exactly four distinguishable outcomes, one row per click:
--   approved | rejected | link_expired | link_already_used
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS governance.approval_actions (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    approval_inbox_id   uuid REFERENCES governance.approval_inbox(id),
    gate_decision_id    uuid,
    outcome             text NOT NULL,
    reason              text NOT NULL,
    principal_id        text,
    principal_name      text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT approval_actions_outcome_allowed
        CHECK (outcome IN ('approved', 'rejected', 'link_expired', 'link_already_used'))
);

-- ---------------------------------------------------------------------
-- publish_attempts — Publisher-owned immutable audit. Exactly one row per
-- publish attempt, on every branch (published or rejected-with-reason).
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS governance.publish_attempts (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_run_id        uuid,
    gate_decision_id    uuid,
    function_id         text,
    jti                 text,
    content_hash        text,
    outcome             text NOT NULL,
    reason              text NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT publish_attempts_outcome_allowed
        CHECK (outcome IN ('published', 'rejected'))
);

-- ---------------------------------------------------------------------
-- jti_ledger — durable single-use gate-token ledger.
--
-- Durability matters: Container Apps replicas scale out and restart, so an
-- in-process set would let a replayed jti through on a different replica.
-- The primary key is the enforcement mechanism (INSERT ... ON CONFLICT DO
-- NOTHING); a zero-row result means "already consumed" -> replay.
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS governance.jti_ledger (
    jti                 text PRIMARY KEY,
    gate_decision_id    uuid,
    consumed_at         timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- Indexes
-- ---------------------------------------------------------------------

CREATE INDEX IF NOT EXISTS idx_kill_switches_active
    ON governance.kill_switches(active);
CREATE INDEX IF NOT EXISTS idx_kill_switches_scope_function_id
    ON governance.kill_switches(scope, function_id);
CREATE INDEX IF NOT EXISTS idx_approval_inbox_status
    ON governance.approval_inbox(status);
CREATE INDEX IF NOT EXISTS idx_approval_inbox_agent_run_id
    ON governance.approval_inbox(agent_run_id);
CREATE INDEX IF NOT EXISTS idx_approval_actions_inbox_id
    ON governance.approval_actions(approval_inbox_id);
CREATE INDEX IF NOT EXISTS idx_publish_attempts_created_at
    ON governance.publish_attempts(created_at);
CREATE INDEX IF NOT EXISTS idx_publish_attempts_jti
    ON governance.publish_attempts(jti);

INSERT INTO governance.schema_migrations (version)
VALUES ('0001_governance_init')
ON CONFLICT (version) DO NOTHING;

COMMIT;
