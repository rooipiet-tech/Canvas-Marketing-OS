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
// MAIDEN-DEPLOY INCIDENT (2026-08-01): this job's initial create failed
// with InvalidParameterValueInContainerTemplate / UNAUTHORIZED on the
// `image` field — ARM validates a container template's image pullability
// at create time, and with NO `registries[]` block at all, it attempts an
// anonymous pull against the private ACR, which 401s regardless of any
// identity separately attached via `identity.userAssignedIdentities`.
// This is NOT the L-0049/L-0061 IdentityDoesNotExist failure (that never
// occurred here — the job got past identity attachment and failed later,
// specifically on image validation) and this job's situation differs from
// mcp-smoke-job.bicep's in the way that matters: id-orchestrator is a
// long-established identity (managed-identity.bicep, already used
// successfully by ca-orchestrator, ca-gatekeeper, and this job's own
// sibling modules.orchestrator/smoke-test-job.bicep across prior
// sessions), not a freshly-created identity racing its own propagation
// within this same deployment operation (L-0061's actual root cause, for
// the THEN-freshly-attached id-mcp-web). Fix: mirror
// modules/orchestrator/smoke-test-job.bicep's own, empirically-working
// pattern for this exact identity — `identity.userAssignedIdentities` AND
// `registries[]` referencing it, together, in the same create call.
@description('Azure region.')
param location string = resourceGroup().location

@description('Container Apps Job name.')
param jobName string = 'caj-loop-e2e-smoke'

@description('Resource id of the Container Apps managed environment (cae-cmos-dev).')
param environmentId string

@description('Shared ACR login server (infra/modules/container-registry.bicep output) — see the MAIDEN-DEPLOY INCIDENT comment above.')
param acrLoginServer string

@description('Resource id of the pre-provisioned user-assigned managed identity (managed-identity.bicep\'s id-orchestrator), reused unmodified from modules/orchestrator/smoke-test-job.bicep\'s identical param.')
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
  // UserAssignedIdentity (id-orchestrator) referenced by registries[]
  // below for ACR pull — mirrors smoke-test-job.bicep's identical,
  // already-working pattern for this exact identity (see the
  // MAIDEN-DEPLOY INCIDENT comment above for why this differs from
  // mcp-smoke-job.bicep's genuinely-fresh-identity situation).
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
      registries: [
        {
          server: acrLoginServer
          identity: userAssignedIdentityId
        }
      ]
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
