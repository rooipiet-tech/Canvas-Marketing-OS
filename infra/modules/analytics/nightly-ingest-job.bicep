// Canvas Marketing OS — infra/modules/analytics/nightly-ingest-job.bicep
//
// caj-analytics-nightly-ingest — the REAL nightly execution mechanism
// (AC-26). A Schedule-triggered Container Apps Job — genuinely
// unprecedented in this repo (every existing Job uses triggerType
// 'Manual'; the domain-expert-recommended default, an additive Logic App
// recurrence trigger under infra/modules/scheduling, is structurally
// unreachable because AC-01's frozen touch-scope regex excludes
// infra/modules/scheduling/* entirely — see
// docs/analytics-nightly-scheduling.md's "Schedule-trigger rationale"
// section, accepted by the plan-reviewer per user amendment 3).
//
// cronExpression '0 1 * * *' = 01:00 UTC / 03:00 SAST nightly.
//
// Image-bootstrap contract (L-0060/L-0061, standing): NO `identity` block
// and NO `registries[]` block at all in this Bicep resource — a
// Microsoft.App/jobs resource cannot have any user-assigned identity
// newly attached in its own initial create call. `image` defaults to a
// public MCR placeholder needing no registry auth at all.
// .github/workflows/analytics-image.yml's gated deploy job is the SOLE,
// EXCLUSIVE attacher of id-analytics to this job, every deploy, via the
// documented 2-step Microsoft workaround: `az containerapp job identity
// assign` immediately followed by `az containerapp job registry set`,
// before `az containerapp job update --image` (pinned to the building
// commit's SHA, never `:latest`).
//
// Container command invokes `cli.py nightly` (the FULL pipeline: ingest
// all 4 sources -> reconcile_utm -> all 4 rollups -> export_fabric_day ->
// blob upload — see services/analytics-ingest/analytics_ingest/cli.py),
// NEVER bare `run --source all` (which per that file's own docstring only
// ever ingests, silently skipping reconciliation/rollups/export/upload
// every night — the exact F1 gap the plan-reviewer flagged and this
// build fixes).

@description('Azure region.')
param location string = resourceGroup().location

@description('Container Apps Job name.')
param jobName string = 'caj-analytics-nightly-ingest'

@description('Resource id of the Container Apps managed environment (cae-cmos-dev).')
param environmentId string

@description('Postgres server fully-qualified domain name.')
param postgresFqdn string

@description('Postgres administrator login.')
param administratorLogin string

@secure()
@description('Postgres administrator login password.')
param administratorLoginPassword string

@description('Postgres database name to connect to.')
param databaseName string = 'postgres'

@description('Key Vault URI, for the credentials.py dual-mode secret-resolution fallback path.')
param keyVaultUri string

@description('Vault service internal base URL (analytics_ingest.vault_client.VAULT_API_URL) — never a hardcoded FQDN literal (AC-30).')
param vaultApiUrl string

@description('Shared storage account name backing the nightly Fabric export blob container.')
param storageAccountName string

@description('Blob container name for the nightly Fabric shortcut export.')
param blobContainerName string = 'analytics-fabric-export'

@description('analytics-ingest container image reference. Defaults to a public MCR placeholder needing no registry auth at all — see this file\'s header comment. Only analytics-image.yml\'s gated deploy job (via `az containerapp job update --image`) ever sets a real, SHA-pinned image.')
param image string = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

var databaseUrl = 'postgresql://${administratorLogin}:${administratorLoginPassword}@${postgresFqdn}:5432/${databaseName}?sslmode=require'

resource nightlyIngestJob 'Microsoft.App/jobs@2024-03-01' = {
  name: jobName
  location: location
  // Deliberately NO identity block at all — see this file's header
  // comment and L-0061.
  tags: {
    purpose: 'nightly analytics-ingest pipeline (Schedule-triggered)'
  }
  properties: {
    environmentId: environmentId
    configuration: {
      triggerType: 'Schedule'
      replicaTimeout: 1800
      replicaRetryLimit: 1
      scheduleTriggerConfig: {
        cronExpression: '0 1 * * *'
        replicaCompletionCount: 1
        parallelism: 1
      }
      // Deliberately NO registries[] block — see this file's header comment.
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
          name: 'analytics-nightly-ingest'
          image: image
          command: [
            'sh'
            '-c'
            'python -m analytics_ingest.cli nightly --day $(date -u -d yesterday +%Y-%m-%d)'
          ]
          env: [
            {
              name: 'DATABASE_URL'
              secretRef: 'db-connection-string'
            }
            {
              name: 'KEY_VAULT_URI'
              value: keyVaultUri
            }
            {
              name: 'VAULT_API_URL'
              value: vaultApiUrl
            }
            {
              name: 'STORAGE_ACCOUNT_NAME'
              value: storageAccountName
            }
            {
              name: 'BLOB_CONTAINER_NAME'
              value: blobContainerName
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

output jobName string = nightlyIngestJob.name
output jobId string = nightlyIngestJob.id
