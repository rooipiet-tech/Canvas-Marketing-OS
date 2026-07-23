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
// SCHEMA_SQL is the already-loaded schema.sql content, passed down as a
// plain string parameter from main.bicep's loadTextContent call — this
// module does not call loadTextContent itself.

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
          name: 'schema-sql'
          value: schemaSql
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
            'printf "%s" "$SCHEMA_SQL" > /tmp/schema.sql && psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f /tmp/schema.sql'
          ]
          env: [
            {
              name: 'DATABASE_URL'
              secretRef: 'db-connection-string'
            }
            {
              name: 'SCHEMA_SQL'
              secretRef: 'schema-sql'
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
