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
// migrationSql is services/vault/migrations/0001_vault_internal_init.sql's
// content, already loaded by main.bicep via loadTextContent and threaded
// down as a plain parameter — this module does not call loadTextContent
// itself (same convention as migration-job.bicep's schemaSql).

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

@secure()
@description('Full contents of services/vault/migrations/0001_vault_internal_init.sql, loaded by main.bicep via loadTextContent.')
param migrationSql string

var databaseUrl = 'postgresql://${administratorLogin}:${administratorLoginPassword}@${postgresFqdn}:5432/${databaseName}?sslmode=require'

// See migration-job.bicep's header: base64-encoding sidesteps Container
// Apps' "$$" -> "$" secret-value collapse, which would otherwise corrupt
// this file's "DO $$ ... $$" PL/pgSQL dollar-quoting.
var migrationSqlBase64 = base64(migrationSql)

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
          name: 'migration-sql-b64'
          value: migrationSqlBase64
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

output jobName string = sidecarMigrationJob.name
output jobId string = sidecarMigrationJob.id
