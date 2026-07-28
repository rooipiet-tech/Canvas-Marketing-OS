-- Canvas Marketing OS — orchestrator service schema (0001_orchestrator_init)
--
-- Additive `public`-schema tables (C1): the orchestrator owns
-- task_state + task_transitions as the sole system of record for
-- retry_count and per-transition state history, independent of Vault's
-- best-effort agent_runs write (orchestrator/vault_client.py). This never
-- touches the frozen public-schema tables defined by
-- contracts/vault-schema/schema.sql — contracts/.frozen-v1.sha256 keeps
-- guarding that file unmodified.
--
-- Idempotent: every statement uses IF NOT EXISTS guards, so this file can
-- be safely re-applied against an already-migrated database (verified in
-- CI by applying it twice in a row per AC-015).
--
-- Applied through a new in-VNet Container Apps Job
-- (infra/modules/orchestrator/migration-job.bicep, caj-orchestrator-migrate)
-- mirroring infra/modules/migration-job.bicep's base64-encoded-SQL-secret
-- pattern (sidesteps Container Apps' "$$" -> "$" secret-value collapse —
-- this file contains no PL/pgSQL dollar-quoting, but the same base64
-- encoding is applied defensively regardless, matching the vault
-- sidecar-migration-job.bicep precedent's own stated rationale).
--
-- No column in either table stores client/personal-data-shaped content —
-- only ids, short enum-like text, a jsonb array of task_id references,
-- integer counters, and timestamps (AC-020, C8).

BEGIN;

-- ---------------------------------------------------------------------
-- task_state — one row per decomposed task. retry_count and
-- vault_write_failed_count are both orchestrator-owned counters,
-- independent of the Service Bus transport's own maxDeliveryCount (C5).
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS task_state (
    task_id                   uuid PRIMARY KEY,
    loop_id                   text NOT NULL,
    task_type                 text NOT NULL,
    state                     text NOT NULL,
    retry_count               integer NOT NULL DEFAULT 0,
    depends_on                jsonb NOT NULL DEFAULT '[]'::jsonb,
    vault_write_failed_count  integer NOT NULL DEFAULT 0,
    created_at                timestamptz NOT NULL DEFAULT now(),
    updated_at                timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- task_transitions — one row per state transition (or, for
-- vault_write_failed, one no-op audit row where from_state = to_state),
-- forming the queryable audit trail AC-014/AC-016b require. `reason` is a
-- CHECK-constrained closed vocabulary mirroring orchestrator/models.py's
-- TransitionReason enum exactly (F6 — structural on both sides, not a
-- naming convention alone).
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS task_transitions (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id      uuid NOT NULL REFERENCES task_state(task_id),
    from_state   text,
    to_state     text NOT NULL,
    reason       text NOT NULL CHECK (reason IN (
                     'created',
                     'dependency_satisfied',
                     'dispatched',
                     'completed',
                     'failed_attempt_1',
                     'failed_attempt_2',
                     'dead_lettered',
                     'vault_write_failed'
                 )),
    occurred_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_task_transitions_task_id ON task_transitions(task_id);

COMMIT;
