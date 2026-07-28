// Canvas Marketing OS — infra/modules/orchestrator/managed-identity.bicep
//
// id-orchestrator — a USER-ASSIGNED managed identity shared by every
// orchestrator Container App / Job that pulls its image from the shared
// ACR at creation time (ca-orchestrator, caj-orchestrator-smoke-test).
//
// Same chicken-and-egg ordering fix as infra/modules/vault/managed-identity.bicep
// (L-0020): a SYSTEM-assigned identity's principalId only exists once its
// owning resource has actually been created, so an AcrPull role assignment
// scoped to that principalId can only be created AFTER that resource — but
// the resource needs to pull its image from the same ACR (via that same
// AcrPull grant) in order to be created in the first place. Confirmed live
// for ca-vault under the identical pattern (20+ min "Operation expired",
// zero role assignments ever created).
//
// A user-assigned identity's principalId/resourceId exists independently
// of anything that later references it, so this module creates the
// identity and grants it AcrPull with NO dependency on the Container
// App/Job that will use it — it provisions first, and the image-pull-time
// chicken-and-egg is broken.
//
// caj-orchestrator-migrate (image: postgres:16) does not pull from the
// shared ACR at all, so it is not subject to this ordering bug and is not
// wired to this identity.

@description('Azure region.')
param location string = resourceGroup().location

@description('User-assigned managed identity name.')
param identityName string = 'id-orchestrator'

@description('Shared ACR resource name, for the AcrPull role assignment scope.')
param acrRegistryName string

@description('Resource id of the shared ACR (informational — role assignment scope is resolved from acrRegistryName).')
param acrRegistryId string

resource acrRegistry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: acrRegistryName
}

resource orchestratorIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: identityName
  location: location
}

// Granted here, independently of any resource that will later use this
// identity to pull the orchestrator image — the grant exists and is
// active before any Container App/Job referencing this identity is ever
// created, so image pull at creation time always has a valid AcrPull
// grant to use.
resource orchestratorIdentityAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acrRegistryId, orchestratorIdentity.name, 'AcrPull')
  scope: acrRegistry
  properties: {
    principalId: orchestratorIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '7f951dda-4ed3-4680-a7ca-43fe172d538d'
    )
  }
}

output identityId string = orchestratorIdentity.id
output identityPrincipalId string = orchestratorIdentity.properties.principalId
output identityClientId string = orchestratorIdentity.properties.clientId
