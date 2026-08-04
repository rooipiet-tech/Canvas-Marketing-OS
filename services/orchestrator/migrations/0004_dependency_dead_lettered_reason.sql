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
--
-- deploy-infra #75 (4 Aug 2026, heartbeat round 14): this migration's
-- original ADD CONSTRAINT (validating against the full existing table)
-- failed against live cmos-dev data with "check constraint
-- task_transitions_reason_check ... is violated by some row". Every
-- application code path that writes task_transitions.reason goes through
-- the closed TransitionReason enum (orchestrator/models.py) via
-- db.transition/db.increment_vault_write_failure's `.value` serialization
-- -- confirmed by inspection, there is no other INSERT INTO
-- task_transitions anywhere in the codebase -- so a live app bug writing
-- an invalid value is ruled out. The offending row(s) are pre-existing
-- audit-trail history predating this closed vocabulary (task_transitions
-- is append-only; nothing rewrites old rows to satisfy constraints added
-- later). Two changes:
--   1. A read-only diagnostic DO block (below) RAISEs one NOTICE per
--      distinct offending reason value + row count, captured in Container
--      Apps' console logs exactly like this job's other NOTICE/CREATE
--      TABLE lines -- there is no other way to see cmos-dev's actual data
--      from outside the VNet, and this makes the failure mode legible
--      instead of a bare "some row" if it ever recurs (e.g. after a
--      future 0005 adds yet another reason value).
--   2. ADD CONSTRAINT ... NOT VALID (standard Postgres practice for
--      adding a CHECK constraint to a table with pre-existing history it
--      shouldn't have to retroactively satisfy): enforces the full closed
--      vocabulary for every INSERT/UPDATE from this point forward (zero
--      loss of the F6 defense-in-depth guarantee for anything the running
--      application writes), without requiring historical audit rows —
--      written before this value existed — to be rewritten or dropped.

BEGIN;

DO $$
DECLARE
    rec RECORD;
BEGIN
    FOR rec IN
        SELECT reason, count(*) AS n
        FROM task_transitions
        WHERE reason NOT IN (
            'created', 'dependency_satisfied', 'dispatched', 'completed',
            'failed_attempt_1', 'failed_attempt_2', 'dead_lettered',
            'vault_write_failed', 'qa_blocked', 'dependency_dead_lettered'
        )
        GROUP BY reason
    LOOP
        RAISE NOTICE 'task_transitions_reason_check offending value: reason=% count=%', rec.reason, rec.n;
    END LOOP;
END $$;

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
)) NOT VALID;

COMMIT;
