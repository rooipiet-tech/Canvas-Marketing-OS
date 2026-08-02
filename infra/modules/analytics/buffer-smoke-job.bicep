// Canvas Marketing OS — infra/modules/analytics/buffer-smoke-job.bicep
//
// caj-analytics-buffer-smoke — the gated one-shot live Buffer GraphQL
// introspection smoke test (user amendment 2 / AC-34, L-0026/L-0064): the
// Buffer GraphQL query shape buffer_client.py assumes
// (ASSUMED_METRIC_FIELDS) must be verified LIVE against api.buffer.com
// before any live nightly run, never assumed from docs/memory. Triggered
// at deploy time like every other service's smoke test
// (analytics-image.yml's gated deploy job) — separate from, and NEVER
// gating, CI (fixture-based tests remain the sole CI/PR gate).
//
// Runs `python -m analytics_ingest.buffer_introspect` — see that module's
// assert_expected_fields(), which fails loudly (never a silent pass) if
// the live schema is missing any assumed field.
//
// Image-bootstrap contract (L-0060/L-0061, standing) — identical to
// nightly-ingest-job.bicep: NO `identity` block, NO `registries[]` block.
// analytics-image.yml's gated deploy job attaches id-analytics via the
// same 2-step CLI workaround (`job identity assign` then `job registry
// set`) before setting the real, SHA-pinned image. At runtime, this
// job's DefaultAzureCredential fallback (analytics_ingest.credentials
// .resolve_secret) reads buffer-api-key via id-analytics's Key Vault
// Secrets User grant (infra/modules/analytics/main.bicep) — no
// BUFFER_API_KEY env value baked into Bicep at all.

@description('Azure region.')
param location string = resourceGroup().location

@description('Container Apps Job name.')
param jobName string = 'caj-analytics-buffer-smoke'

@description('Resource id of the Container Apps managed environment (cae-cmos-dev).')
param environmentId string

@description('Key Vault URI, for the credentials.py dual-mode secret-resolution fallback path (buffer-api-key).')
param keyVaultUri string

@description('Buffer GraphQL API base URL.')
param bufferApiUrl string = 'https://api.buffer.com/graphql'

@description('analytics-ingest container image reference. Defaults to a public MCR placeholder needing no registry auth at all — see this file\'s header comment. Only analytics-image.yml\'s gated deploy job (via `az containerapp job update --image`) ever sets a real, SHA-pinned image.')
param image string = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

resource bufferSmokeJob 'Microsoft.App/jobs@2024-03-01' = {
  name: jobName
  location: location
  // Deliberately NO identity block at all — see this file's header
  // comment and L-0061.
  tags: {
    purpose: 'gated one-shot live Buffer GraphQL introspection smoke test'
  }
  properties: {
    environmentId: environmentId
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: 300
      replicaRetryLimit: 0
      manualTriggerConfig: {
        replicaCompletionCount: 1
        parallelism: 1
      }
      // Deliberately NO registries[] block — see this file's header comment.
    }
    template: {
      containers: [
        {
          name: 'analytics-buffer-smoke'
          image: image
          command: [
            'python'
            '-m'
            'analytics_ingest.buffer_introspect'
          ]
          env: [
            {
              name: 'KEY_VAULT_URI'
              value: keyVaultUri
            }
            {
              name: 'BUFFER_API_URL'
              value: bufferApiUrl
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

output jobName string = bufferSmokeJob.name
output jobId string = bufferSmokeJob.id
