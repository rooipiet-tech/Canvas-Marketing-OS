// Canvas Marketing OS — infra/modules/mcp/mcp-smoke-job.bicep
//
// One-shot Container Apps Job (caj-mcp-smoke) running the MCP conformance
// suite (AC-1's pytest -m mcp_conformance, same test file) in fixture
// mode over HTTP against the just-deployed mcp-* Container Apps' internal
// FQDNs — the in-VNet smoke step deploy-mcp.yml's deploy job ends with
// (AC-20). Same one-shot-job mechanism as caj-vault-migrate/
// caj-vault-query/caj-mcp-ops-migrate (Manual trigger, replicaRetryLimit
// 1) — a fourth instance of the established pattern, not a new one.
//
// Image pull identity: reuses one of the three mcp-* app identities
// (passed in as userAssignedIdentityId) rather than minting a fourth
// identity resource — this job needs no Key Vault access at all (its
// conformance calls run in fixture mode, no live secrets), only AcrPull,
// which that identity already holds (see acr-role-assignment.bicep).

@description('Azure region.')
param location string = resourceGroup().location

@description('Container Apps Job name.')
param jobName string = 'caj-mcp-smoke'

@description('Resource id of the Container Apps managed environment (cae-cmos-dev).')
param environmentId string

@description('Full container image reference for the mcp-smoke test image (see mcp/Dockerfile.smoke).')
param image string

@description('Login server of the Azure Container Registry the image is pulled from.')
param registryLoginServer string

@description('Resource id of a user-assigned managed identity already granted AcrPull (reuses one of the mcp-* app identities).')
param userAssignedIdentityId string

@description('Internal base URL of the deployed mcp-web Container App.')
param mcpWebBaseUrl string

@description('Internal base URL of the deployed mcp-buffer Container App.')
param mcpBufferBaseUrl string

@description('Internal base URL of the deployed mcp-canva Container App.')
param mcpCanvaBaseUrl string

resource mcpSmokeJob 'Microsoft.App/jobs@2024-03-01' = {
  name: jobName
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${userAssignedIdentityId}': {}
    }
  }
  tags: {
    purpose: 'one-shot in-VNet MCP conformance smoke test'
  }
  properties: {
    environmentId: environmentId
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: 600
      replicaRetryLimit: 1
      manualTriggerConfig: {
        replicaCompletionCount: 1
        parallelism: 1
      }
      registries: [
        {
          server: registryLoginServer
          identity: userAssignedIdentityId
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'mcp-smoke'
          image: image
          command: [
            'pytest'
            '-m'
            'mcp_conformance'
            '/app/tests'
            '-v'
          ]
          env: [
            {
              name: 'MCP_WEB_BASE_URL'
              value: mcpWebBaseUrl
            }
            {
              name: 'MCP_BUFFER_BASE_URL'
              value: mcpBufferBaseUrl
            }
            {
              name: 'MCP_CANVA_BASE_URL'
              value: mcpCanvaBaseUrl
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

output jobName string = mcpSmokeJob.name
output jobId string = mcpSmokeJob.id
