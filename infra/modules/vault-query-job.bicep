// Canvas Marketing OS — vault-query-job.bicep
//
// A second one-shot Container Apps Job, caj-vault-query, in the same
// cae-cmos-dev environment (VNet-reachable), used to run ad hoc
// read-only queries against the private-endpoint-only Postgres server
// (satisfies SUCCESS-002 without a bastion/jumpbox). Same mechanism as
// caj-vault-migrate — a second job, not a different mechanism — per the
// locked Container Apps Job decision.
//
// Start with a query override via `--yaml` (confirmed live — see
// .github/workflows/deploy-gateway.yml's "Seed a real agent_run" step):
//   cat > /tmp/query.yaml <<'YAML'
//   properties:
//     template:
//       containers:
//         - name: vault-query
//           image: postgres:16
//           command: [sh, -c, 'psql "$DATABASE_URL" -Atc "$QUERY"']
//           env:
//             - name: DATABASE_URL
//               secretRef: db-connection-string
//             - name: QUERY
//               value: "select ... ;"
//           resources: {cpu: 0.5, memory: 1Gi}
//   YAML
//   az containerapp job start -g cmos-dev -n caj-vault-query --yaml /tmp/query.yaml
//   A bare `--env-vars QUERY=...` does NOT work here, even with --image
//   added: any Container Argument (--env-vars/--command/etc.) makes the CLI
//   replace the container's ENTIRE spec rather than patch one field, so
//   --env-vars alone silently drops both this template's `command` (the
//   psql invocation) and the DATABASE_URL secretRef, and --env-vars without
//   --image fails outright with ERROR (ContainerAppImageRequired). Only a
//   full --yaml override that restates image + command + both env vars
//   reliably overrides just the query.
// Read results with:
//   az containerapp job execution list -g cmos-dev -n caj-vault-query
//   az containerapp job logs show -g cmos-dev -n caj-vault-query --execution <name>

@description('Azure region.')
param location string = resourceGroup().location

@description('Container Apps Job name.')
param jobName string = 'caj-vault-query'

@description('Resource id of the Container Apps managed environment (cae-cmos-dev).')
param environmentId string

@description('Postgres server fully-qualified domain name.')
param postgresFqdn string

@description('Postgres administrator login.')
param administratorLogin string

@secure()
@description('Postgres administrator login password.')
param administratorLoginPassword string

@description('Postgres database name to connect to for the query.')
param databaseName string = 'postgres'

@description('Default ad hoc query, overridable at job-start time via --env-vars QUERY=...')
param defaultQuery string = 'select table_name from information_schema.tables where table_schema=\'public\' order by 1;'

var databaseUrl = 'postgresql://${administratorLogin}:${administratorLoginPassword}@${postgresFqdn}:5432/${databaseName}?sslmode=require'

resource vaultQueryJob 'Microsoft.App/jobs@2024-03-01' = {
  name: jobName
  location: location
  tags: {
    purpose: 'ad hoc read-only Vault query'
  }
  properties: {
    environmentId: environmentId
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: 300
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
      ]
    }
    template: {
      containers: [
        {
          name: 'vault-query'
          image: 'postgres:16'
          command: [
            'sh'
            '-c'
            'psql "$DATABASE_URL" -Atc "$QUERY"'
          ]
          env: [
            {
              name: 'DATABASE_URL'
              secretRef: 'db-connection-string'
            }
            {
              name: 'QUERY'
              value: defaultQuery
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

output jobName string = vaultQueryJob.name
output jobId string = vaultQueryJob.id
