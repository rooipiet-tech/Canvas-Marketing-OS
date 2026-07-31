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
// (id-mcp-web, attached post-create by deploy-mcp.yml) rather than
// minting a fourth identity resource — this job needs no Key Vault
// access at all (its conformance calls run in fixture mode, no live
// secrets), only AcrPull, which that identity already holds (see
// acr-role-assignment.bicep).
//
// MAIDEN-DEPLOY INCIDENT — ROUND 2 (L-0049/L-0061 class, 2026-07-31):
// removing only `registries[]` (round 1's fix, matching container-app.bicep
// and ca-console's own working fix) was NOT sufficient for this resource
// type: caj-mcp-smoke still failed provisioning with IdentityDoesNotExist
// even with zero `registries[]` block, because this module still attached
// id-mcp-web via `identity.userAssignedIdentities` in the SAME initial
// `Microsoft.App/jobs` create call. Confirmed: Microsoft.App/jobs is
// stricter than Microsoft.App/containerApps for this platform limitation
// (microsoft/azure-container-apps#1467) — a job apparently cannot have
// ANY user-assigned identity newly attached at create time at all, not
// just one referenced by registries[] (ca-orchestrator's own smoke-test-job
// module, which HAS attached a fresh identity + registries[] and deployed
// clean, contributes no counter-evidence here since it was never
// re-tested after this discovery — see L-0061 for the full writeup). This
// module
// therefore declares NO `identity` block whatsoever at create time — the
// job is created with no managed identity at all. deploy-mcp.yml's gated
// deploy job is the ONLY thing that ever attaches an identity to this job
// at all, via the documented 2-step Microsoft workaround for #1467: `az
// containerapp job identity assign --user-assigned <id>` (attach),
// immediately followed by `az containerapp job registry set --identity
// <id>` (configure ACR pull auth using that now-attached identity) — both
// idempotent, run every deploy, before the image update.
// `image` follows the same L-0048 bootstrap contract: defaults to a
// public MCR placeholder at the call site; only deploy-mcp.yml (via `az
// containerapp job update --image`) ever sets the real, SHA-pinned
// mcp-smoke image.

@description('Azure region.')
param location string = resourceGroup().location

@description('Container Apps Job name.')
param jobName string = 'caj-mcp-smoke'

@description('Resource id of the Container Apps managed environment (cae-cmos-dev).')
param environmentId string

@description('Full container image reference for the mcp-smoke test image (see mcp/Dockerfile.smoke). Defaults to a public MCR placeholder at the call site — see the MAIDEN-DEPLOY INCIDENT header comment above.')
param image string

@description('Internal base URL of the deployed mcp-web Container App.')
param mcpWebBaseUrl string

@description('Internal base URL of the deployed mcp-buffer Container App.')
param mcpBufferBaseUrl string

@description('Internal base URL of the deployed mcp-canva Container App.')
param mcpCanvaBaseUrl string

resource mcpSmokeJob 'Microsoft.App/jobs@2024-03-01' = {
  name: jobName
  location: location
  // Deliberately NO identity block at all — see the MAIDEN-DEPLOY
  // INCIDENT — ROUND 2 comment above. deploy-mcp.yml's `az containerapp
  // job identity assign` is the sole, exclusive attacher of id-mcp-web to
  // this job, every deploy, before `job registry set`/`job update`.
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
      // Deliberately NO registries[] block — see the MAIDEN-DEPLOY
      // INCIDENT comment on the `image` param above.
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
