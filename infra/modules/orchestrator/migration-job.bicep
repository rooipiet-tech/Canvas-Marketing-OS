// Canvas Marketing OS — infra/modules/orchestrator/migration-job.bicep
//
// caj-orchestrator-migrate — mirrors infra/modules/migration-job.bicep's
// base64-encoded-secret Container Apps Job pattern exactly (same fix for
// Container Apps' "$$" secret-value collapse bug; see that file's header
// comment). A one-shot job in the same cae-cmos-dev environment, applying
// services/orchestrator/migrations/0001_orchestrator_init.sql (additive
// public-schema task_state/task_transitions tables, C1) instead of the
// frozen Vault public schema.
//
// migrationSql is services/orchestrator/migrations/0001_orchestrator_init.sql's
// content, already loaded by main.bicep via loadTextContent and threaded
// down as a plain parameter — this module does not call loadTextContent
// itself (same convention as migration-job.bicep's schemaSql).

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

@secure()
@description('Full contents of services/orchestrator/migrations/0001_orchestrator_init.sql, loaded by main.bicep via loadTextContent.')
param migrationSql string

var databaseUrl = 'postgresql://${administratorLogin}:${administratorLoginPassword}@${postgresFqdn}:5432/${databaseName}?sslmode=require'

// See migration-job.bicep's header: base64-encoding sidesteps Container
// Apps' "$$" -> "$" secret-value collapse. This migration file has no
// PL/pgSQL dollar-quoting, but the same defensive encoding is applied
// regardless, matching the vault sidecar-migration-job.bicep precedent.
var migrationSqlBase64 = base64(migrationSql)

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
          name: 'migration-sql-b64'
          value: migrationSqlBase64
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

output jobName string = orchestratorMigrationJob.name
output jobId string = orchestratorMigrationJob.id
