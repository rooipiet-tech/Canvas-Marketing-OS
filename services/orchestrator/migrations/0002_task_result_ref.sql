-- Canvas Marketing OS — orchestrator service schema (0002_task_result_ref)
--
-- Additive to migrations/0001_orchestrator_init.sql: a single nullable
-- `result_ref jsonb` column on task_state, used by the 5 new dispatch
-- handlers (orchestrator/dispatch.py) to record a SMALL, structured
-- pointer to whatever downstream artifact a task produced — a Vault
-- asset/brief/signal id, a content hash, a gate-decision id, a short enum
-- status string. Never raw content, never client/personal data (same
-- discipline as migrations/0001_orchestrator_init.sql's own header note
-- and AC-020/C8): a later task's handler resolves what to do with its
-- predecessor's output by reading result_ref off the depends_on lineage,
-- not by re-deriving/guessing it.
--
-- Idempotent: IF NOT EXISTS guard, safe to re-apply against an
-- already-migrated database (mirrors 0001's own convention).

BEGIN;

ALTER TABLE task_state ADD COLUMN IF NOT EXISTS result_ref jsonb;

COMMIT;
