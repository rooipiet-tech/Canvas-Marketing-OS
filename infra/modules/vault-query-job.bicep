// Canvas Marketing OS — vault-query-job.bicep
//
// A second one-shot Container Apps Job, caj-vault-query, in the same
// cae-cmos-dev environment (VNet-reachable), used to run ad hoc
// read-only queries against the private-endpoint-only Postgres server
// (satisfies SUCCESS-002 without a bastion/jumpbox). Same mechanism as
// caj-vault-migrate — a second job, not a different mechanism — per the
// locked Container Apps Job decision.
//
// Start with a query override via `az rest` directly against the ARM
// `/start` action (confirmed live — see .github/workflows/deploy-gateway.yml's
// "Seed a real agent_run" step):
//   jq -n --arg query 'select ... ;' '{
//     containers: [{
//       name: "vault-query", image: "postgres:16",
//       command: ["sh", "-c", "psql \"$DATABASE_URL\" -Atc \"$QUERY\""],
//       env: [
//         {name: "DATABASE_URL", secretRef: "db-connection-string"},
//         {name: "QUERY", value: $query}
//       ],
//       resources: {cpu: 0.5, memory: "1Gi"}
//     }]
//   }' > /tmp/query.json
//   az rest --method post --url \
//     "https://management.azure.com/subscriptions/{subscriptionId}/resourceGroups/cmos-dev/providers/Microsoft.App/jobs/caj-vault-query/start?api-version=2024-03-01" \
//     --body @/tmp/query.json
//   Two CLI paths do NOT work here, both confirmed live:
//   (1) A bare `--env-vars QUERY=...` (`az containerapp job start --env-vars
//   ...`) makes the CLI replace the container's ENTIRE spec rather than
//   patch one field — silently dropping `command` (the psql invocation) and
//   the DATABASE_URL secretRef, and failing outright with
//   ERROR (ContainerAppImageRequired) without --image (L-0022).
//   (2) `az containerapp job start --yaml <file>` with a `properties: /
//   template: / containers:`-shaped file (the natural guess, matching
//   `job create`/`update`'s own resource shape) is WORSE: the CLI accepts it
//   with no error, but silently ignores the override and runs the job's
//   unmodified PERSISTED template instead — the execution still reports
//   `Succeeded` (this job's default query is itself a harmless valid
//   SELECT), so nothing about the outcome reveals the override never
//   applied. Confirmed directly: POSTing that same shape to `/start` via
//   `az rest` 400s with "Unknown properties template in
//   StartJobExecutionTemplate are not supported" — the REST action's real
//   body type has NO `properties`/`template` wrapper, just
//   `containers`/`initContainers` at the top level, and the CLI's `--yaml`
//   translation for `job start` doesn't surface that mismatch as an error
//   (L-0023). Going straight to `az rest` with the verified bare shape
//   sidesteps the CLI's translation entirely.
// Read results with:
//   az containerapp job execution list -g cmos-dev -n caj-vault-query
//   az containerapp job logs show -g cmos-dev -n caj-vault-query --execution <name> --container vault-query
//   (--container is REQUIRED — `job logs show` errors without it even for a
//   single-container job.)

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
