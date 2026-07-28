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
// migrationSql is passed down as a plain string parameter from
// main.bicep's loadTextContent call — this module never calls
// loadTextContent itself, matching the repo convention.

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

@secure()
@description('Full contents of infra/modules/governance/migrations/0001_governance_init.sql, loaded by main.bicep via loadTextContent.')
param migrationSql string

var databaseUrl = 'postgresql://${administratorLogin}:${administratorLoginPassword}@${postgresFqdn}:5432/${databaseName}?sslmode=require'

var migrationSqlBase64 = base64(migrationSql)

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
          name: 'governance-sql-b64'
          value: migrationSqlBase64
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
            'printf "%s" "$GOVERNANCE_SQL_B64" | base64 -d > /tmp/governance.sql && psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f /tmp/governance.sql'
          ]
          env: [
            {
              name: 'DATABASE_URL'
              secretRef: 'db-connection-string'
            }
            {
              name: 'GOVERNANCE_SQL_B64'
              secretRef: 'governance-sql-b64'
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
