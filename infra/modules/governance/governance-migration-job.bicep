// Canvas Marketing OS — governance-migration-job.bicep
//
// Applies infra/modules/governance/migrations/0001_governance_init.sql
// (the additive `governance` Postgres schema) using EXACTLY the mechanism
// migration-job.bicep already established for the frozen Vault schema:
// a one-shot, Manual-trigger Container Apps Job running the stock
// postgres:16 image inside cae-cmos-dev (VNet-integrated), so it reaches
// the private-endpoint-only Postgres server with no public firewall
// exception. No second migration mechanism is introduced.
//
// Container Apps job secrets/env values silently collapse "$$" to a
// literal "$". The migration SQL is therefore base64-encoded before it
// becomes a job secret (the base64 alphabet has no "$"), exactly as
// migration-job.bicep does. 0001_governance_init.sql additionally
// contains zero dollar signs by construction (CHECK constraints instead
// of DO $$ ... $$ CREATE TYPE blocks), so this is belt-and-braces.
//
// TD-15 fix: this used to receive one already-joined `migrationSql`
// string (0001+0002 concatenated) and run it unconditionally on every
// deploy. It now receives `migrationBundleBase64` — a bundle of
// {version, sql} pairs built by main.bicep — and `runnerScript`, the
// shared infra/modules/migration-ledger-runner.sh, which skips any
// version already recorded in governance.schema_migrations instead of
// re-running it (that table already existed for record-keeping —
// 0001_governance_init.sql's own INSERT — this is the first thing that
// actually reads it to gate execution). See migration-ledger-runner.sh's
// own header for the bundle format and the full TD-15 rationale.

@description('Azure region.')
param location string = resourceGroup().location

@description('Container Apps Job name.')
param jobName string = 'caj-governance-migrate'

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

@description('Ledger table\'s schema (created if missing).')
param ledgerSchema string = 'governance'

@description('Ledger table\'s unqualified name.')
param ledgerTable string = 'schema_migrations'

@secure()
@description('Bundle of {version, sql} pairs — one per infra/modules/governance/migrations/*.sql file — built by main.bicep and consumed by migration-ledger-runner.sh. See that script\'s header for the exact wire format.')
param migrationBundleBase64 string

var databaseUrl = 'postgresql://${administratorLogin}:${administratorLoginPassword}@${postgresFqdn}:5432/${databaseName}?sslmode=require'

resource governanceMigrationJob 'Microsoft.App/jobs@2024-03-01' = {
  name: jobName
  location: location
  tags: {
    purpose: 'one-shot governance schema migration'
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
          name: 'governance-migrate'
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

output jobName string = governanceMigrationJob.name
output jobId string = governanceMigrationJob.id
