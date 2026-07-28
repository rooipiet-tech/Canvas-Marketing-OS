// Canvas Marketing OS — migration-job.bicep
//
// LOCKED DECISION (budget owner): the in-VNet Vault-schema migration
// mechanism is a one-shot Container Apps Job — NOT a Bicep
// deploymentScript, NOT a self-hosted GitHub runner. This job runs
// inside cae-cmos-dev (VNet-integrated) so it can reach the
// private-endpoint-only Postgres server without any public firewall
// exception ever being opened on Postgres (INFRA-009).
//
// Credential: DATABASE_URL is built from the administratorLoginPassword
// secure parameter threaded down from main.bicep (no Key Vault
// round-trip in this credential's critical path, per plan F6).
// schemaSql is the already-loaded schema.sql content, passed down as a
// plain string parameter from main.bicep's loadTextContent call — this
// module does not call loadTextContent itself. It is base64-encoded
// locally (see schemaSqlBase64 below) before becoming a job secret.
//
// SCHEMA_SQL is stored as the SECRET base64(schemaSql), not the raw DDL
// text, and decoded in-container before use. Azure Container Apps resolves
// `$(...)`-shaped references in secret/env values and treats `$$` as an
// escape for a literal `$` — so the DDL's own `DO $$ ... END $$;`
// dollar-quote blocks were silently collapsed to a single `$`, producing a
// psql syntax error ("syntax error at or near '$'") on every real deploy,
// even though the identical file applies cleanly when CI feeds it to psql
// directly (CI never goes through this env-var round-trip, so it never hit
// this). Base64 has no `$` in its alphabet, so it survives Container Apps'
// substitution engine untouched. See .compound learning L-0012.

@description('Azure region.')
param location string = resourceGroup().location

@description('Container Apps Job name.')
param jobName string = 'caj-vault-migrate'

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
@description('Full contents of contracts/vault-schema/schema.sql, loaded by main.bicep via loadTextContent.')
param schemaSql string

var databaseUrl = 'postgresql://${administratorLogin}:${administratorLoginPassword}@${postgresFqdn}:5432/${databaseName}?sslmode=require'

// Container Apps job secrets/env values silently collapse "$$" to a
// literal "$" (its escape sequence for a literal dollar sign). schema.sql
// uses "$$ ... $$" PL/pgSQL dollar-quoting, which that collapse corrupts
// into a single "$" — a syntax error at runtime. Base64-encoding the SQL
// before it becomes a secret value sidesteps this: the base64 alphabet
// has no "$", so nothing is left for Container Apps to "escape".
var schemaSqlBase64 = base64(schemaSql)

resource migrationJob 'Microsoft.App/jobs@2024-03-01' = {
  name: jobName
  location: location
  tags: {
    purpose: 'one-shot Vault schema migration'
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
          name: 'vault-migrate'
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
