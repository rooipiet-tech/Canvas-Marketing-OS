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

@secure()
@description('Full contents of services/analytics-ingest/migrations/0001_analytics_init.sql, loaded by main.bicep via loadTextContent.')
param migrationSql string

var databaseUrl = 'postgresql://${administratorLogin}:${administratorLoginPassword}@${postgresFqdn}:5432/${databaseName}?sslmode=require'

// Same defensive base64-encoding fix as infra/modules/migration-job.bicep
// (Container Apps job secrets/env values silently collapse "$$" to a
// literal "$", which would corrupt any future PL/pgSQL dollar-quoting
// added to this migration file).
var migrationSqlBase64 = base64(migrationSql)

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
          name: 'schema-sql-b64'
          value: migrationSqlBase64
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
            'printf "%s" "$SCHEMA_SQL_B64" | base64 -d > /tmp/schema.sql && psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f /tmp/schema.sql'
          ]
          env: [
            {
              name: 'DATABASE_URL'
              secretRef: 'db-connection-string'
            }
            {
              name: 'SCHEMA_SQL_B64'
              secretRef: 'schema-sql-b64'
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
