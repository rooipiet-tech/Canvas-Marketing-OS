// Canvas Marketing OS — infra/modules/vault/options-inbox-migration-job.bicep
//
// caj-vault-options-inbox-migrate — Appendix D PR 1. Same base64-encoded-
// secret Container Apps Job pattern as sidecar-migration-job.bicep (see
// that file's header for the "$$" secret-value collapse fix this mirrors).
// A third one-shot job in the same cae-cmos-dev environment, applying
// services/vault/migrations/0002_options_inbox_init.sql — the
// option_cards / approval_decisions / standing_permissions tables — to
// the public schema, not the vault_internal sidecar schema.
//
// TD-15 fix: this used to receive one already-joined `migrationSql`
// string (0002+0003 concatenated) and run it unconditionally on every
// deploy. It now receives `migrationBundleBase64` — a bundle of
// {version, sql} pairs built by main.bicep — and `runnerScript`, the
// shared infra/modules/migration-ledger-runner.sh, which skips any
// version already recorded in
// public.vault_options_inbox_schema_migrations instead of re-running it.
// A distinct ledger table name from every other job that also targets
// `public` (orchestrator, gateway) — see migration-ledger-runner.sh's own
// header for the bundle format and the full TD-15 rationale.
//
// No identity block — a Microsoft.App/jobs resource takes none at its
// initial create (L-0061).

@description('Azure region.')
param location string = resourceGroup().location

@description('Container Apps Job name.')
param jobName string = 'caj-vault-options-inbox-migrate'

@description('Resource id of the Container Apps managed environment (cae-cmos-dev).')
param environmentId string

@description('Postgres server fully-qualified domain name.')
param postgresFqdn string

@description('Postgres administrator login.')
param administratorLogin string

@secure()
@description('Postgres administrator login password.')
param administratorLoginPassword string

@description('Postgres database name to connect to for the migration.')
param databaseName string = 'postgres'

@description('Shared migration-ledger runner script (infra/modules/migration-ledger-runner.sh), loaded by main.bicep via loadTextContent and passed as a plain container command literal — not a secret, matching infra/modules/governance/gatekeeper-app.bicep\'s unpackScript convention.')
param runnerScript string

@description('Ledger table\'s schema (created if missing). "public" — option_cards/approval_decisions/standing_permissions already live there.')
param ledgerSchema string = 'public'

@description('Ledger table\'s unqualified name. Distinct from every other service\'s ledger table sharing the same "public" schema, so they can never collide.')
param ledgerTable string = 'vault_options_inbox_schema_migrations'

@secure()
@description('Bundle of {version, sql} pairs — one per services/vault/migrations/000{2,3}_*.sql file — built by main.bicep and consumed by migration-ledger-runner.sh. See that script\'s header for the exact wire format.')
param migrationBundleBase64 string

var databaseUrl = 'postgresql://${administratorLogin}:${administratorLoginPassword}@${postgresFqdn}:5432/${databaseName}?sslmode=require'

resource optionsInboxMigrationJob 'Microsoft.App/jobs@2024-03-01' = {
  name: jobName
  location: location
  tags: {
    purpose: 'one-shot options_inbox (option_cards/approval_decisions/standing_permissions) migration'
  }
  properties: {
    environmentId: environmentId
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: 900
      replicaRetryLimit: 1
      manualTriggerConfig: {
        replicaCompletionCount: 1
        parallelism: 1
      }
      secrets: [
        {
          name: 'db-connection-string'
          value: databaseUrl
        }
        {
          name: 'migration-bundle-b64'
          value: migrationBundleBase64
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'vault-options-inbox-migrate'
          image: 'postgres:16'
          command: [
            'sh'
            '-c'
            runnerScript
          ]
          env: [
            {
              name: 'DATABASE_URL'
              secretRef: 'db-connection-string'
            }
            {
              name: 'MIGRATION_BUNDLE_B64'
              secretRef: 'migration-bundle-b64'
            }
            {
              name: 'LEDGER_SCHEMA'
              value: ledgerSchema
            }
            {
              name: 'LEDGER_TABLE'
              value: ledgerTable
            }
          ]
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
        }
      ]
    }
  }
}

output jobName string = optionsInboxMigrationJob.name
output jobId string = optionsInboxMigrationJob.id
