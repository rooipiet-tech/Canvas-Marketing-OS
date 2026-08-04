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
--
-- deploy-infra #75/#77 (4 Aug 2026, heartbeat round 14): THIS statement --
-- not 0004's -- turned out to be the actual root cause of "check
-- constraint task_transitions_reason_check ... is violated by some row".
-- migration.sql concatenates 0001-0004 into ONE psql -f script run with
-- ON_ERROR_STOP=1, and EVERY deploy-infra run unconditionally re-applies
-- ALL FOUR files in order (see infra/main.bicep's orchestratorMigrationSql
-- var comment) -- so this file's DROP+ADD CONSTRAINT re-validates the
-- FULL live table on every single deploy, not just the first time. Once
-- production had real rows with reason='dependency_dead_lettered'
-- (state_machine.cascade_dead_letter, F-DISPATCH-CASCADE, live since
-- heartbeat attempt 12), THIS file's 9-value list -- which predates that
-- value and does not include it -- started failing its own re-validation
-- on every subsequent deploy, aborting the script before 0004 (which DOES
-- know about 'dependency_dead_lettered') ever got a chance to run and
-- supersede it. A first attempted fix scoped only to 0004 (this same
-- date) had zero effect for exactly this reason: 0004 was never reached.
-- Same fix as that 0004 attempt, applied where it actually matters: ADD
-- CONSTRAINT ... NOT VALID (standard Postgres pattern -- ADD CONSTRAINT
-- normally validates every existing row; NOT VALID skips that initial
-- scan while still fully enforcing the constraint for every INSERT/UPDATE
-- from this point forward) so this statement can no longer be broken by
-- data written under a LATER migration's wider vocabulary. 0004 still
-- immediately supersedes this constraint with its own (also NOT VALID)
-- version in the same script run, so this change is purely about not
-- blocking that supersession.

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
)) NOT VALID;

COMMIT;
