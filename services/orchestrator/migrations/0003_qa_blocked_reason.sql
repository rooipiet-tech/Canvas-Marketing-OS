-- Canvas Marketing OS — orchestrator service schema (0003_qa_blocked_reason)
--
-- Additive to migrations/0001_orchestrator_init.sql /
-- 0002_task_result_ref.sql: extends task_transitions.reason's CHECK
-- constraint with one new value, 'qa_blocked' — the terminal reason
-- dispatch.py's qa-review handler (plan step 10) records when function 02
-- (Brand Steward QA) returns pass=false (a normal, expected business
-- outcome — a seeded missing-UTM violation, an uncleared client
-- reference — NOT an infrastructure failure, so it belongs in the
-- FAILED-state reason vocabulary as its own distinct value rather than
-- being force-fit into failed_attempt_1/2, which are specifically about
-- the retry state machine, or dead_lettered, which implies 3 retries were
-- already exhausted).
--
-- Idempotent: DROP CONSTRAINT IF EXISTS + ADD CONSTRAINT is safe to
-- re-run any number of times against an already-migrated database
-- (mirrors 0001/0002's own idempotency discipline).

BEGIN;

ALTER TABLE task_transitions DROP CONSTRAINT IF EXISTS task_transitions_reason_check;

ALTER TABLE task_transitions ADD CONSTRAINT task_transitions_reason_check CHECK (reason IN (
    'created',
    'dependency_satisfied',
    'dispatched',
    'completed',
    'failed_attempt_1',
    'failed_attempt_2',
    'dead_lettered',
    'vault_write_failed',
    'qa_blocked'
));

COMMIT;
