#!/bin/sh
# Canvas Marketing OS - shared migration ledger runner (TD-15).
#
# THE single source of truth for how a "join every numbered migration file
# into one psql -f run" job decides what to actually run. Passed as the
# container command literal (loadTextContent()'d by infra/main.bicep and
# handed to each *-migration-job.bicep module as the `runnerScript` param,
# same convention as infra/modules/governance/gatekeeper-bundle-unpack.sh's
# unpackScript) -- never reimplemented per job.
#
# WHY THIS EXISTS: every migration job in this repo used to concatenate
# ALL of a service's numbered migration files into one `psql -f` run,
# unconditionally, on EVERY deploy-infra. That re-validates already-applied
# DDL against whatever the live table looks like TODAY, not what it looked
# like when that file first ran -- which is exactly what took production
# down once: services/orchestrator/migrations/0003_qa_blocked_reason.sql's
# DROP + ADD CONSTRAINT re-checked the full live task_transitions table on
# every deploy, and once migrations/0004_dependency_dead_lettered_reason.sql
# had added rows using a value 0003's own (older, 9-value) CHECK list didn't
# know about, 0003's re-validation started failing on every subsequent
# deploy -- aborting the script (ON_ERROR_STOP=1) before 0004 ever got a
# chance to run and supersede it. This script makes that class of re-run
# impossible: once a version is recorded in the ledger table, it is never
# executed again, no matter what the live data looks like by then.
#
# Bundle format ($MIGRATION_BUNDLE_B64, base64-decoded below): a sequence of
# two-line records, one per migration file, in the order the file should be
# considered --
#   MIGRATION:<version>
#   <base64 of that one file's full, self-contained BEGIN/COMMIT SQL>
# -- built by main.bicep's <service>MigrationBundle var. <version> is the
# migration's filename with its .sql extension stripped (e.g.
# "0001_orchestrator_init"), matching the literal version strings
# infra/modules/governance/migrations/*.sql already INSERTs into
# governance.schema_migrations -- this script does not invent a second
# naming convention.
#
# Every SQL secret value in a migration job is base64-encoded before
# becoming a Container Apps secret (this bundle included) -- Container Apps
# silently collapses a literal "$$" to "$" in secret values, which would
# otherwise corrupt PL/pgSQL dollar-quoting (see
# infra/modules/migration-job.bicep's header for the original fix this
# mirrors). This script itself is passed as a plain `command` literal, not
# a secret, so it is not subject to that collapse and is not itself
# base64-encoded.
#
# Contract:
#   DATABASE_URL           (required) postgresql://... connection string
#   LEDGER_SCHEMA           (required) schema the ledger table lives in
#                           (created if missing -- e.g. "public",
#                           "governance", "vault_internal", "analytics")
#   LEDGER_TABLE            (required) unqualified ledger table name (e.g.
#                           "schema_migrations", "orchestrator_schema_migrations"
#                           -- distinct per service where the schema is
#                           shared, e.g. "public", so two services' ledgers
#                           can never collide)
#   MIGRATION_BUNDLE_B64    (required) the encoded bundle described above

set -eu

: "${DATABASE_URL:?DATABASE_URL must be set}"
: "${LEDGER_SCHEMA:?LEDGER_SCHEMA must be set}"
: "${LEDGER_TABLE:?LEDGER_TABLE must be set}"
: "${MIGRATION_BUNDLE_B64:?MIGRATION_BUNDLE_B64 must be set}"

printf '%s' "$MIGRATION_BUNDLE_B64" | base64 -d > /tmp/migration_bundle.txt

echo "migration-ledger-runner: ensuring ledger table \"$LEDGER_SCHEMA\".\"$LEDGER_TABLE\" exists"
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 <<SQLEOF
CREATE SCHEMA IF NOT EXISTS "$LEDGER_SCHEMA";
CREATE TABLE IF NOT EXISTS "$LEDGER_SCHEMA"."$LEDGER_TABLE" (
    version     text PRIMARY KEY,
    applied_at  timestamptz NOT NULL DEFAULT now()
);
SQLEOF

apply_one() {
  version="$1"
  b64="$2"

  already=$(psql "$DATABASE_URL" -tAc \
    "SELECT 1 FROM \"$LEDGER_SCHEMA\".\"$LEDGER_TABLE\" WHERE version = '$version'")

  if [ "$already" = "1" ]; then
    echo "migration-ledger-runner: skip (already recorded): $version"
    return 0
  fi

  echo "migration-ledger-runner: applying: $version"
  printf '%s' "$b64" | base64 -d > /tmp/migration_current.sql
  # Recorded in the SAME psql invocation as the migration's own SQL (not a
  # separate step afterward), so a version can only ever be marked applied
  # once its DDL has actually run in this process.
  cat >> /tmp/migration_current.sql <<SQLEOF
INSERT INTO "$LEDGER_SCHEMA"."$LEDGER_TABLE" (version) VALUES ('$version') ON CONFLICT (version) DO NOTHING;
SQLEOF
  psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f /tmp/migration_current.sql
  echo "migration-ledger-runner: applied: $version"
}

current_version=""
while IFS= read -r line || [ -n "$line" ]; do
  case "$line" in
    MIGRATION:*)
      current_version="${line#MIGRATION:}"
      ;;
    *)
      if [ -n "$current_version" ]; then
        apply_one "$current_version" "$line"
        current_version=""
      fi
      ;;
  esac
done < /tmp/migration_bundle.txt

echo "migration-ledger-runner: done"
