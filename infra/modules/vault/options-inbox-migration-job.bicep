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
// migrationSql is 0002_options_inbox_init.sql's content, loaded by
// main.bicep via loadTextContent and threaded down as a plain parameter,
// same convention as sidecar-migration-job.bicep's own migrationSql.
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

@secure()
@description('Full contents of services/vault/migrations/0002_options_inbox_init.sql, loaded by main.bicep via loadTextContent.')
param migrationSql string

var databaseUrl = 'postgresql://${administratorLogin}:${administratorLoginPassword}@${postgresFqdn}:5432/${databaseName}?sslmode=require'

// See sidecar-migration-job.bicep's header: base64-encoding sidesteps
// Container Apps' "$$" -> "$" secret-value collapse. This migration
// contains no dollar-quoted PL/pgSQL, but the encoding is applied
// unconditionally, matching every other migration job in this repo.
var migrationSqlBase64 = base64(migrationSql)

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
          name: 'migration-sql-b64'
          value: migrationSqlBase64
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
            'printf "%s" "$MIGRATION_SQL_B64" | base64 -d > /tmp/migration.sql && psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f /tmp/migration.sql'
          ]
          env: [
            {
              name: 'DATABASE_URL'
              secretRef: 'db-connection-string'
            }
            {
              name: 'MIGRATION_SQL_B64'
              secretRef: 'migration-sql-b64'
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
