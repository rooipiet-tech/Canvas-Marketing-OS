// Canvas Marketing OS — infra/modules/orchestrator/loop-e2e-smoke-job.bicep
//
// caj-loop-e2e-smoke (plan step 20; AC-18): a one-shot Container Apps Job
// running orchestrator/loop_e2e_smoke.py (`python -m
// orchestrator.loop_e2e_smoke`, shipped in the same orchestrator image
// caj-orchestrator-smoke-test already uses) from inside cae-cmos-dev's
// VNet. Publishes a synthetic daily-signal-loop heartbeat onto the REAL
// `event` queue, then polls the LIVE deployed ca-orchestrator app's own
// GET /runs/{task_ref} endpoint (step 17) until the S8 proof circuit
// (draft-content -> qa-review -> request-approval) reaches a terminal
// state — signal->brief->draft->QA->approval-card-created is this
// smoke's success condition (the GOAL's own wording); the Buffer write
// stays dry-run regardless, structurally enforced by Publisher's own
// dry-run-force logic (step 14), never invoked by this smoke at all.
//
// Only needs to PUBLISH the heartbeat, not receive from the task queue —
// same "Data Sender only" identity scoping as
// modules/orchestrator/smoke-test-job.bicep's existing
// caj-orchestrator-smoke-test.
//
// L-0048/L-0049/L-0060 CAPSTONE bootstrap contract: this job's image is
// the ALREADY-LIVE orchestratorImage (not a separately-CI-built new
// image), and its userAssignedIdentityId REUSES the existing
// id-orchestrator identity (managed-identity.bicep), which already holds
// AcrPull independently. Deliberately NO `registries[]` block here
// regardless — mirrors infra/modules/mcp/mcp-smoke-job.bicep's identical
// choice (the safer, uniform convention every new Container App/Job in
// this repo now follows): the deploy workflow's own `az containerapp job
// registry set` step is the sole, every-run setter of the ACR-pull
// identity.

@description('Azure region.')
param location string = resourceGroup().location

@description('Container Apps Job name.')
param jobName string = 'caj-loop-e2e-smoke'

@description('Resource id of the Container Apps managed environment (cae-cmos-dev).')
param environmentId string

@description('Resource id of a user-assigned managed identity (reuses id-orchestrator). ACR pull is attached exclusively by the deploy workflow via `az containerapp job registry set`, never by this template.')
param userAssignedIdentityId string

@description('Orchestrator service image reference — the SAME value passed to container-app.bicep/smoke-test-job.bicep.')
param orchestratorImage string

@description('Live internal /runs URL base of the deployed ca-orchestrator app, e.g. http://<internalFqdn>/runs.')
param orchestratorRunsUrl string

@description('Existing Service Bus namespace name (infra/modules/service-bus.bicep output).')
param serviceBusNamespaceName string

resource serviceBusNamespace 'Microsoft.ServiceBus/namespaces@2022-10-01-preview' existing = {
  name: serviceBusNamespaceName
}

resource loopE2eSmokeJob 'Microsoft.App/jobs@2024-03-01' = {
  name: jobName
  location: location
  // 'SystemAssigned, UserAssigned' -- matches modules/orchestrator/
  // smoke-test-job.bicep's identical pattern exactly: the SystemAssigned
  // half is what gives this resource a `.identity.principalId` to grant
  // the Service Bus Data Sender role assignment below to; the
  // UserAssignedIdentity (id-orchestrator) is attached for ACR-pull
  // purposes only, and — since NO `registries[]` block below ever
  // references it — attaching it here alongside SystemAssigned in the
  // same create call does not reproduce the L-0049 bug (that bug is
  // specifically about a registries[] reference in the SAME call, not
  // mere identity attachment).
  identity: {
    type: 'SystemAssigned, UserAssigned'
    userAssignedIdentities: {
      '${userAssignedIdentityId}': {}
    }
  }
  tags: {
    purpose: 'S8 proof-circuit live smoke test: heartbeat injection, poll GET /runs until terminal (AC-18)'
  }
  properties: {
    environmentId: environmentId
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: 900
      replicaRetryLimit: 0
      manualTriggerConfig: {
        replicaCompletionCount: 1
        parallelism: 1
      }
      // Deliberately NO registries[] block — see the header comment above.
    }
    template: {
      containers: [
        {
          name: 'loop-e2e-smoke'
          image: orchestratorImage
          command: [
            'python'
            '-m'
            'orchestrator.loop_e2e_smoke'
          ]
          env: [
            {
              name: 'ORCHESTRATOR_RUNS_URL'
              value: orchestratorRunsUrl
            }
            {
              name: 'SERVICE_BUS_NAMESPACE'
              value: '${serviceBusNamespaceName}.servicebus.windows.net'
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

// AC-024-equivalent: Azure Service Bus Data Sender — same role GUID
// modules/orchestrator/smoke-test-job.bicep already uses (69a216fc-b8fb-
// 44d8-bc22-1f3c2cd27a39), live-verified there via
// services/orchestrator/scripts/verify_rbac_guids.py.
resource loopE2eSmokeJobServiceBusDataSender 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(serviceBusNamespace.id, loopE2eSmokeJob.name, 'Azure Service Bus Data Sender')
  scope: serviceBusNamespace
  properties: {
    principalId: loopE2eSmokeJob.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '69a216fc-b8fb-44d8-bc22-1f3c2cd27a39'
    )
  }
}

output jobName string = loopE2eSmokeJob.name
output jobId string = loopE2eSmokeJob.id
