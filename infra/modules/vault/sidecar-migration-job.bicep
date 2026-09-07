// Canvas Marketing OS — infra/modules/vault/sidecar-migration-job.bicep
//
// caj-vault-sidecar-migrate — mirrors infra/modules/migration-job.bicep's
// base64-encoded-secret Container Apps Job pattern exactly (same fix for
// Container Apps' "$$" secret-value collapse bug corrupting PL/pgSQL
// dollar-quoting; see that file's header comment and
// services/vault/migrations/0001_vault_internal_init.sql's header). A
// second one-shot job in the same cae-cmos-dev environment, applying the
// vault_internal sidecar migration instead of the frozen public schema.
//
// TD-15 fix: this job now shares infra/modules/migration-ledger-runner.sh
// (the same runner every other migration job in this repo now uses) via
// the `runnerScript` param, and receives `migrationBundleBase64` — a
// bundle of {version, sql} pairs built by main.bicep, here containing the
// single 0001_vault_internal_init.sql entry — instead of a raw
// `migrationSql` string. This file has only ever had one migration, so it
// never hit TD-15's actual failure mode, but it now records itself in
// vault_internal.schema_migrations like every other job, so a future
// second file here is safe by construction rather than by discipline.

@description('Azure region.')
param location string = resourceGroup().location

@description('Container Apps Job name.')
param jobName string = 'caj-vault-sidecar-migrate'

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

@description('Ledger table\'s schema (created if missing). "vault_internal" — this migration\'s own schema.')
param ledgerSchema string = 'vault_internal'

@description('Ledger table\'s unqualified name.')
param ledgerTable string = 'schema_migrations'

@secure()
@description('Bundle of {version, sql} pairs — one per services/vault/migrations/0001_vault_internal_init.sql file — built by main.bicep and consumed by migration-ledger-runner.sh. See that script\'s header for the exact wire format.')
param migrationBundleBase64 string

var databaseUrl = 'postgresql://${administratorLogin}:${administratorLoginPassword}@${postgresFqdn}:5432/${databaseName}?sslmode=require'

resource sidecarMigrationJob 'Microsoft.App/jobs@2024-03-01' = {
  name: jobName
  location: location
  tags: {
    purpose: 'one-shot vault_internal sidecar schema migration'
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
          name: 'vault-sidecar-migrate'
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

output jobName string = sidecarMigrationJob.name
output jobId string = sidecarMigrationJob.id
