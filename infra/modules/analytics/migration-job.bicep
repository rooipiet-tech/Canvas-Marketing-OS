// Canvas Marketing OS — infra/modules/analytics/migration-job.bicep
//
// caj-analytics-migrate — one-shot Container Apps Job applying
// services/analytics-ingest/migrations/0001_analytics_init.sql (the
// `analytics` Postgres schema this session owns; contracts/vault-schema/
// schema.sql is NEVER touched — see that migration file's own header).
// Byte-for-byte mirrors infra/modules/migration-job.bicep's structure:
// image postgres:16 (no ACR pull needed, no identity block at all),
// DATABASE_URL built from administratorLogin/administratorLoginPassword/
// postgresFqdn threaded down from main.bicep (no Key Vault round-trip),
// migrationSql base64-encoded into a job secret to dodge Container Apps'
// "$$" -> "$" collapse (this migration has no dollar-quoting today, but
// the encoding is applied defensively regardless, matching every other
// migration-job in this repo).
//
// Explicitly EXCLUDED from the shared analytics-ingest:<sha> image that
// nightly-ingest-job.bicep/buffer-smoke-job.bicep consume (built by
// .github/workflows/analytics-image.yml) — this job runs postgres:16
// forever and is never touched by that workflow's deploy job or its
// identity/registry/image-update loop.
//
// TD-15 fix: this job now shares infra/modules/migration-ledger-runner.sh
// (the same runner every other migration job in this repo now uses) via
// the `runnerScript` param, and receives `migrationBundleBase64` — a
// bundle of {version, sql} pairs built by main.bicep, here containing the
// single 0001_analytics_init.sql entry — instead of a raw `migrationSql`
// string. This file has only ever had one migration, so it never hit
// TD-15's actual failure mode, but it now records itself in
// analytics.schema_migrations like every other job, so a future second
// file here is safe by construction rather than by discipline.

@description('Azure region.')
param location string = resourceGroup().location

@description('Container Apps Job name.')
param jobName string = 'caj-analytics-migrate'

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

@description('Ledger table\'s schema (created if missing). "analytics" — this migration\'s own schema.')
param ledgerSchema string = 'analytics'

@description('Ledger table\'s unqualified name.')
param ledgerTable string = 'schema_migrations'

@secure()
@description('Bundle of {version, sql} pairs — one per services/analytics-ingest/migrations/0001_analytics_init.sql file — built by main.bicep and consumed by migration-ledger-runner.sh. See that script\'s header for the exact wire format.')
param migrationBundleBase64 string

var databaseUrl = 'postgresql://${administratorLogin}:${administratorLoginPassword}@${postgresFqdn}:5432/${databaseName}?sslmode=require'

resource migrationJob 'Microsoft.App/jobs@2024-03-01' = {
  name: jobName
  location: location
  // Deliberately NO identity block — image is postgres:16 (public MCR
  // image, no ACR pull needed), same as infra/modules/migration-job.bicep.
  tags: {
    purpose: 'one-shot analytics-ingest schema migration'
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
          name: 'analytics-migrate'
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

output jobName string = migrationJob.name
output jobId string = migrationJob.id
