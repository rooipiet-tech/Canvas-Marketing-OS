// Canvas Marketing OS — infra/modules/orchestrator/migration-job.bicep
//
// caj-orchestrator-migrate — mirrors infra/modules/migration-job.bicep's
// base64-encoded-secret Container Apps Job pattern exactly (same fix for
// Container Apps' "$$" secret-value collapse bug; see that file's header
// comment). A one-shot job in the same cae-cmos-dev environment, applying
// services/orchestrator/migrations/*.sql (additive public-schema
// task_state/task_transitions tables, C1, and everything since) instead
// of the frozen Vault public schema.
//
// TD-15 fix: this used to receive one already-joined `migrationSql`
// string (all of 0001-0005 concatenated) and run it unconditionally on
// every deploy — the mechanism that took production down once (see
// services/orchestrator/migrations/0003_qa_blocked_reason.sql's own
// incident comment). It now receives `migrationBundleBase64` — a bundle
// of {version, sql} pairs, one per migration file, built by main.bicep —
// and `runnerScript`, the shared
// infra/modules/migration-ledger-runner.sh, which skips any version
// already recorded in public.orchestrator_schema_migrations instead of
// re-running it. Neither this module nor main.bicep reimplements that
// skip logic; see migration-ledger-runner.sh's own header for the bundle
// format and the full rationale.

@description('Azure region.')
param location string = resourceGroup().location

@description('Container Apps Job name.')
param jobName string = 'caj-orchestrator-migrate'

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

@description('Ledger table\'s schema (created if missing). "public" — orchestrator\'s tables already live there unqualified.')
param ledgerSchema string = 'public'

@description('Ledger table\'s unqualified name. Distinct from every other service\'s ledger table sharing the same "public" schema, so they can never collide.')
param ledgerTable string = 'orchestrator_schema_migrations'

@secure()
@description('Bundle of {version, sql} pairs — one per services/orchestrator/migrations/*.sql file — built by main.bicep and consumed by migration-ledger-runner.sh. See that script\'s header for the exact wire format.')
param migrationBundleBase64 string

var databaseUrl = 'postgresql://${administratorLogin}:${administratorLoginPassword}@${postgresFqdn}:5432/${databaseName}?sslmode=require'

resource orchestratorMigrationJob 'Microsoft.App/jobs@2024-03-01' = {
  name: jobName
  location: location
  tags: {
    purpose: 'one-shot orchestrator schema migration'
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
          name: 'orchestrator-migrate'
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

output jobName string = orchestratorMigrationJob.name
output jobId string = orchestratorMigrationJob.id
