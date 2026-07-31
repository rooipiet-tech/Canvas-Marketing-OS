// Canvas Marketing OS — infra/modules/orchestrator/container-app.bicep
//
// ca-orchestrator — the orchestrator service Container App. Dual identity
// (SystemAssigned, UserAssigned), internal ingress only (exposes /status +
// /health). Pulls its image from the single shared platform ACR via the
// pre-provisioned id-orchestrator USER-assigned identity (managed-identity.bicep),
// never the ACR admin account, never a second registry (C11) — see that
// module's header for the chicken-and-egg AcrPull ordering bug (L-0020,
// confirmed live for ca-vault) a system-assigned identity's own AcrPull
// grant hits at Container App creation time, which this sidesteps. Reads
// DATABASE_URL from a Container Apps secret built the same way
// migration-job.bicep builds databaseUrl. Talks to the existing Service Bus
// namespace via the SYSTEM-assigned identity only (never a connection
// string/SAS key, C4/disableLocalAuth=true) — granted both "Azure Service
// Bus Data Sender" and "Azure Service Bus Data Receiver" below (AC-024),
// since this app both publishes task envelopes and consumes the
// event/task queues (worker.py). These are runtime-only data-plane reads,
// not image-pull-at-creation-time, so they aren't subject to the same
// ordering bug and can stay on the simpler system-assigned identity —
// same split infra/modules/vault/container-app.bicep uses.

@description('Azure region.')
param location string = resourceGroup().location

@description('Container App name.')
param appName string = 'ca-orchestrator'

@description('Resource id of the Container Apps managed environment (cae-cmos-dev).')
param environmentId string

@description('Shared ACR login server (infra/modules/container-registry.bicep output).')
param acrLoginServer string

@description('Shared ACR resource name (informational — kept for interface parity with the other orchestrator child modules; the AcrPull grant for image pull lives on managed-identity.bicep\'s identity, not scoped here).')
param acrRegistryName string

@description('Resource id of the shared ACR (informational — see acrRegistryName).')
param acrRegistryId string

@description('Resource id of the pre-provisioned user-assigned managed identity (managed-identity.bicep\'s id-orchestrator), used to pull orchestratorImage from acrLoginServer without the system-assigned-identity ordering bug.')
param userAssignedIdentityId string

@description('Orchestrator service image reference, e.g. <acrLoginServer>/orchestrator:<tag>.')
param orchestratorImage string

@description('Postgres server fully-qualified domain name.')
param postgresFqdn string

@description('Postgres administrator login.')
param administratorLogin string

@secure()
@description('Postgres administrator login password.')
param administratorLoginPassword string

@description('Postgres database name to connect to.')
param databaseName string = 'postgres'

@description('Best-effort Vault API base URL (may be unreachable — orchestrator/vault_client.py degrades gracefully per AC-016/AC-017).')
param vaultApiUrl string = ''

@description('Existing Service Bus namespace name (infra/modules/service-bus.bicep output).')
param serviceBusNamespaceName string

@description('Changes on every deploy (main.bicep defaults it to utcNow()) so this app always gets a NEW revision. Same governance-round-4 pattern as vault/container-app.bicep: with activeRevisionsMode Single, a redeploy that only changes a secret VALUE (e.g. a rotated Postgres admin password) does NOT create a new revision — the already-running replica keeps the DATABASE_URL it booted with, indefinitely, even after the live password has changed underneath it. Forcing a fresh revisionSuffix every deploy is what actually restarts the container and picks up the current secret values.')
param deployToken string

var databaseUrl = 'postgresql://${administratorLogin}:${administratorLoginPassword}@${postgresFqdn}:5432/${databaseName}?sslmode=require'

// F5: identical resource type/API version to infra/modules/service-bus.bicep,
// same serviceBusNamespaceName param name/pattern container-app.bicep uses.
resource serviceBusNamespace 'Microsoft.ServiceBus/namespaces@2022-10-01-preview' existing = {
  name: serviceBusNamespaceName
}

resource orchestratorApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: appName
  location: location
  identity: {
    type: 'SystemAssigned, UserAssigned'
    userAssignedIdentities: {
      '${userAssignedIdentityId}': {}
    }
  }
  properties: {
    environmentId: environmentId
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: false
        targetPort: 8000
        transport: 'auto'
      }
      registries: [
        {
          server: acrLoginServer
          identity: userAssignedIdentityId
        }
      ]
      secrets: [
        {
          name: 'database-url'
          value: databaseUrl
        }
      ]
    }
    template: {
      revisionSuffix: 'r${uniqueString(deployToken)}'
      containers: [
        {
          name: 'orchestrator'
          image: orchestratorImage
          env: [
            {
              name: 'DATABASE_URL'
              secretRef: 'database-url'
            }
            {
              name: 'VAULT_API_URL'
              value: vaultApiUrl
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
          probes: [
            {
              type: 'Readiness'
              httpGet: {
                path: '/health'
                port: 8000
              }
              initialDelaySeconds: 5
              periodSeconds: 10
            }
            {
              type: 'Liveness'
              httpGet: {
                path: '/health'
                port: 8000
              }
              initialDelaySeconds: 10
              periodSeconds: 30
            }
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 3
      }
    }
  }
}

// No AcrPull role assignment here — orchestratorApp pulls its image via
// userAssignedIdentityId, which managed-identity.bicep already grants
// AcrPull to, independently and ahead of this resource. See this file's
// header and managed-identity.bicep's header for the ordering bug this
// avoids (L-0020).

// AC-024: Azure Service Bus Data Sender — GUID resolved live via
// services/orchestrator/scripts/verify_rbac_guids.py against this
// subscription's role definitions (see that script's docstring, F2).
resource orchestratorServiceBusDataSender 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(serviceBusNamespace.id, orchestratorApp.name, 'Azure Service Bus Data Sender')
  scope: serviceBusNamespace
  properties: {
    principalId: orchestratorApp.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '69a216fc-b8fb-44d8-bc22-1f3c2cd27a39'
    )
  }
}

// AC-024: Azure Service Bus Data Receiver — GUID resolved live via
// services/orchestrator/scripts/verify_rbac_guids.py against this
// subscription's role definitions (see that script's docstring, F2).
resource orchestratorServiceBusDataReceiver 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(serviceBusNamespace.id, orchestratorApp.name, 'Azure Service Bus Data Receiver')
  scope: serviceBusNamespace
  properties: {
    principalId: orchestratorApp.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '4f6d3b9b-027b-4f4c-9142-0e5a2a2247e0'
    )
  }
}

output appName string = orchestratorApp.name
output appId string = orchestratorApp.id
output internalFqdn string = orchestratorApp.properties.configuration.ingress.fqdn
output principalId string = orchestratorApp.identity.principalId
