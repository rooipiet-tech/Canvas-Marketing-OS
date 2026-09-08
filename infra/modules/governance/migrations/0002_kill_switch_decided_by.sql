-- Canvas Marketing OS — governance schema migration 0002
--
-- Adds `decided_by` to governance.kill_switches so a REST toggle can
-- record WHO flipped the switch, alongside the reason it already stores.
-- This is additive-only (ADD COLUMN IF NOT EXISTS): 0001_governance_init's
-- table, constraints and indexes are untouched, and this file follows the
-- same no-dollar-signs, self-contained BEGIN/COMMIT convention 0001
-- documents (Container Apps Job secret-value handling of literal "$$").
--
-- WHY A COLUMN, NOT A NEW AUDIT TABLE
-- ------------------------------------
-- kill_switches already carries `updated_at` (defaulting to now()) rather
-- than being append-only like gate_decisions/approval_actions — it models
-- CURRENT switch state, one row per (scope, function_id), updated in
-- place on every toggle. The Gatekeeper REST wrapper added alongside this
-- migration (app/routers/kill_switch.py) keeps exactly one row for the
-- global switch and updates it on every POST /kill-switch/toggle, so that
-- same row already IS the "last audit entry" GET /kill-switch/audit/last
-- reports — no second table needed for a governance object this
-- disclosed the design of a decade ago.
--
-- Idempotent: re-running this file against a database that already has
-- the column is a no-op.

BEGIN;

ALTER TABLE governance.kill_switches
    ADD COLUMN IF NOT EXISTS decided_by text;

INSERT INTO governance.schema_migrations (version)
VALUES ('0002_kill_switch_decided_by')
ON CONFLICT (version) DO NOTHING;

COMMIT;
