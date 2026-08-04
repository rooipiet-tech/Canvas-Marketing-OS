-- Canvas Marketing OS — orchestrator service schema (0004_dependency_dead_lettered_reason)
--
-- Additive to migrations/0001_orchestrator_init.sql / 0002_task_result_ref.sql /
-- 0003_qa_blocked_reason.sql: extends task_transitions.reason's CHECK
-- constraint with one new value, 'dependency_dead_lettered' — the reason
-- state_machine.cascade_dead_letter records when a task is dead-lettered
-- immediately because one of its depends_on entries has already reached
-- DEAD_LETTERED and can never complete (F-DISPATCH-CASCADE, 2026-08-04),
-- as opposed to 'dead_lettered' above, which means the task's OWN handler
-- was tried and failed 3 times. Distinct values so task_transitions can
-- tell "we tried and gave up" apart from "we never tried, it was already
-- impossible" — see orchestrator/dispatch.py's DependencyDeadLetteredError
-- and orchestrator/state_machine.py's cascade_dead_letter for the full
-- root-cause writeup (heartbeat attempt 11, deploy-loop-e2e-smoke #25:
-- tasks blocked on a permanently dead-lettered dependency were previously
-- routed through the ordinary 3-strike retry_pending cycle regardless,
-- taking ~15+ minutes to reach the exact same DEAD_LETTERED outcome this
-- reaches immediately).
--
-- Idempotent: DROP CONSTRAINT IF EXISTS + ADD CONSTRAINT is safe to
-- re-run any number of times against an already-migrated database
-- (mirrors 0001/0002/0003's own idempotency discipline).

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
    'qa_blocked',
    'dependency_dead_lettered'
));

COMMIT;
