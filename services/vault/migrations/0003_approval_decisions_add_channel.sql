-- Canvas Marketing OS — approval_decisions.channel (Appendix D PR 3)
--
-- 0002_options_inbox_init.sql shipped approval_decisions without a
-- `channel` column, even though contracts/approval-decision.schema.json
-- already defines one ("teams_card" | "console_inbox" | "digest_email" |
-- "system") — an optional field, so it never failed contract validation,
-- but the gap meant every decision recorded so far would have been
-- unable to say how it arrived. Caught while building the /decide
-- endpoint (services/gatekeeper/app/routers/option_decide.py), which
-- needs to store exactly that. Fixed here, additively, rather than
-- editing 0002 in place, since 0002 may already be applied against a
-- live database by the time this lands.
--
-- Idempotent (ADD COLUMN IF NOT EXISTS), same convention as 0001/0002.
-- Applied through the same options-inbox Container Apps Job as 0002 —
-- infra/main.bicep concatenates the two files into one migrationSql var
-- (the join(...) pattern infra/main.bicep's orchestratorMigrationSql
-- comment already documents and authorises), not a second job.

BEGIN;

ALTER TABLE approval_decisions ADD COLUMN IF NOT EXISTS channel text;

COMMIT;
