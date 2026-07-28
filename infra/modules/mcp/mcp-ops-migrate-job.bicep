// Canvas Marketing OS — infra/modules/mcp/mcp-ops-migrate-job.bicep
//
// One-shot Container Apps Job applying mcp/mcp_ops/schema.sql. Reuses the
// IDENTICAL established shape from infra/modules/migration-job.bicep
// (same base64 dollar-quote guard, Manual trigger, replicaRetryLimit 1)
// per ruling R3 — not a forked mechanism. Invoked exactly like
// caj-vault-migrate:
//   az containerapp job start -g cmos-dev -n caj-mcp-ops-migrate
//
// Credential: DATABASE_URL is built from the administratorLoginPassword
// secure parameter threaded down from main.bicep, same as
// migration-job.bicep — no Key Vault round-trip in this credential's
// critical path.

@description('Azure region.')
param location string = resourceGroup().location

@description('Container Apps Job name.')
param jobName string = 'caj-mcp-ops-migrate'

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
@description('Full contents of mcp/mcp_ops/schema.sql, loaded by infra/main.bicep via loadTextContent.')
param schemaSql string

var databaseUrl = 'postgresql://${administratorLogin}:${administratorLoginPassword}@${postgresFqdn}:5432/${databaseName}?sslmode=require'

// Same base64 guard as migration-job.bicep: Container Apps job secrets
// silently collapse a literal "$$" to "$", which would corrupt
// mcp_ops/schema.sql's "$$ ... $$" PL/pgSQL dollar-quoting (its enum-type
// DO $$ guard). Base64's alphabet has no "$", so nothing is left for
// Container Apps to "escape".
var schemaSqlBase64 = base64(schemaSql)

resource mcpOpsMigrateJob 'Microsoft.App/jobs@2024-03-01' = {
  name: jobName
  location: location
  tags: {
    purpose: 'one-shot mcp_ops schema migration'
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
          value: schemaSqlBase64
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'mcp-ops-migrate'
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

output jobName string = mcpOpsMigrateJob.name
output jobId string = mcpOpsMigrateJob.id
