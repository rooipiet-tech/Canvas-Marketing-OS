// Canvas Marketing OS — infra/modules/orchestrator/smoke-test-job.bicep
//
// caj-orchestrator-smoke-test — runs orchestrator/smoke_test.py (`python -m
// orchestrator.smoke_test`, shipped in the same orchestrator image, AC-028)
// from inside cae-cmos-dev's VNet. Publishes a synthetic daily-signal-loop
// heartbeat onto the REAL `event` queue (this job's own managed identity,
// granted Data Sender below), then polls the LIVE deployed ca-orchestrator
// app's own GET /status endpoint (orchestratorStatusUrl, reached over the
// shared Container Apps environment's internal DNS) until the
// deterministically-predicted task_ids appear, comparing what THAT
// independently-running process reports against the checked-in golden
// file — a genuine live check, not a local self-comparison (F1).
//
// Only needs to PUBLISH the heartbeat, not receive from the task queue, so
// unlike ca-orchestrator itself this job's identity is granted only
// "Azure Service Bus Data Sender", not Data Receiver.

@description('Azure region.')
param location string = resourceGroup().location

@description('Container Apps Job name.')
param jobName string = 'caj-orchestrator-smoke-test'

@description('Resource id of the Container Apps managed environment (cae-cmos-dev).')
param environmentId string

@description('Shared ACR login server (infra/modules/container-registry.bicep output).')
param acrLoginServer string

@description('Shared ACR resource name (informational — kept for interface parity with the other orchestrator child modules; the AcrPull grant for image pull lives on managed-identity.bicep\'s identity, not scoped here).')
param acrRegistryName string

@description('Resource id of the shared ACR (informational — see acrRegistryName).')
param acrRegistryId string

@description('Resource id of the pre-provisioned user-assigned managed identity (managed-identity.bicep\'s id-orchestrator), used to pull orchestratorImage from acrLoginServer without the system-assigned-identity ordering bug (L-0020).')
param userAssignedIdentityId string

@description('Orchestrator service image reference — the SAME value passed to container-app.bicep (F3, threaded from main.bicep\'s orchestratorContainerImage param).')
param orchestratorImage string

@description('Live internal /status URL of the deployed ca-orchestrator app, e.g. http://<internalFqdn>/status.')
param orchestratorStatusUrl string

@description('Existing Service Bus namespace name (infra/modules/service-bus.bicep output).')
param serviceBusNamespaceName string

resource serviceBusNamespace 'Microsoft.ServiceBus/namespaces@2022-10-01-preview' existing = {
  name: serviceBusNamespaceName
}

resource smokeTestJob 'Microsoft.App/jobs@2024-03-01' = {
  name: jobName
  location: location
  identity: {
    type: 'SystemAssigned, UserAssigned'
    userAssignedIdentities: {
      '${userAssignedIdentityId}': {}
    }
  }
  tags: {
    purpose: 'live heartbeat-injection-and-golden-compare smoke test against the deployed ca-orchestrator dev endpoint'
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
          name: 'orchestrator-smoke-test'
          image: orchestratorImage
          command: [
            'python'
            '-m'
            'orchestrator.smoke_test'
          ]
          env: [
            {
              name: 'ORCHESTRATOR_STATUS_URL'
              value: orchestratorStatusUrl
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

// No AcrPull role assignment here — smokeTestJob pulls its image via
// userAssignedIdentityId, which managed-identity.bicep already grants
// AcrPull to, independently and ahead of this resource (L-0020).

// AC-024: Azure Service Bus Data Sender — GUID resolved live via
// services/orchestrator/scripts/verify_rbac_guids.py (F2).
resource smokeTestJobServiceBusDataSender 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(serviceBusNamespace.id, smokeTestJob.name, 'Azure Service Bus Data Sender')
  scope: serviceBusNamespace
  properties: {
    principalId: smokeTestJob.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '69a216fc-b8fb-44d8-bc22-1f3c2cd27a39'
    )
  }
}

output jobName string = smokeTestJob.name
output jobId string = smokeTestJob.id
