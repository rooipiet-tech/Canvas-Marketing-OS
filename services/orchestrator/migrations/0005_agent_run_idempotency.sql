-- Canvas Marketing OS — orchestrator service schema (0005_agent_run_idempotency)
--
-- TD-07 (docs/architecture/09-technical-debt.md): worker.py::_retry_or_
-- dead_letter re-invokes a failed task's handler FUNCTION directly, from
-- the top, up to state_machine.record_failure's 3-strike limit -- its own
-- docstring already admitted "a handler that partially wrote to Vault
-- before failing is not guaranteed idempotent on retry (e.g. a duplicate
-- signal/agent_run row is possible)". A handler that creates a real Vault
-- agent_runs row, calls the model gateway, and then fails on a LATER
-- Vault write previously created a second agent_run -- and, since the
-- gateway call re-ran too, incurred a second model charge -- on every
-- retry attempt.
--
-- agent_run_ledger is the orchestrator-owned idempotency record backing
-- VaultClientExt.create_agent_run_idempotent (orchestrator/clients/
-- vault_client_ext.py). idempotency_key is derived deterministically as
-- uuid5(task_id, f"agent_run:{ordinal}") -- the SAME uuid5-over-a-stable-
-- seed pattern orchestrator/decompose.py's _task_uuid and worker.py's own
-- envelope.agent_run_id already use -- where `ordinal` is the 1st/2nd/...
-- create_agent_run call a single handler invocation makes. Because a
-- retry re-runs the SAME handler code from the top, its Nth call derives
-- the identical key the original attempt's Nth call did, so the second
-- attempt finds the first attempt's real Vault row here and reuses it
-- instead of creating (and re-charging for) a new one.
--
-- Idempotent: IF NOT EXISTS guard, safe to re-apply against an
-- already-migrated database (mirrors every prior migration's convention).
--
-- No column stores client/personal-data-shaped content -- only ids and a
-- timestamp (AC-020, C8, same discipline as 0001's own header note).

BEGIN;

CREATE TABLE IF NOT EXISTS agent_run_ledger (
    idempotency_key  uuid PRIMARY KEY,
    task_id          uuid NOT NULL,
    agent_run_id     uuid NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_agent_run_ledger_task_id ON agent_run_ledger(task_id);

COMMIT;
