// Canvas Marketing OS — infra/modules/analytics/managed-identity.bicep
//
// id-analytics — a USER-ASSIGNED managed identity, provisioned
// independently (no dependsOn on any Job/App that will use it) and
// granted AcrPull immediately, mirroring infra/modules/vault/
// managed-identity.bicep's exact shape and rationale: a SystemAssigned
// identity's principalId only exists once its owning resource has
// actually been created, so an AcrPull role assignment scoped to that
// principalId can only be created AFTER that resource — but the resource
// needs to pull its image from the same ACR (via that same AcrPull grant)
// in order to be created in the first place (L-0020's chicken-and-egg
// ordering bug, confirmed live for ca-vault).
//
// This is the ONE identity caj-analytics-nightly-ingest and
// caj-analytics-buffer-smoke ever attach — exclusively post-create via
// CLI (`az containerapp job identity assign` then `job registry set`),
// NEVER in Bicep (L-0061: a Microsoft.App/jobs resource cannot have any
// user-assigned identity newly attached in its own initial create call at
// all). caj-analytics-migrate (image postgres:16, no ACR pull needed) is
// not wired to this identity at all.

@description('Azure region.')
param location string = resourceGroup().location

@description('User-assigned managed identity name.')
param identityName string = 'id-analytics'

@description('Shared ACR resource name, for the AcrPull role assignment scope.')
param acrRegistryName string

@description('Resource id of the shared ACR (informational — role assignment scope is resolved from acrRegistryName).')
param acrRegistryId string

resource acrRegistry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: acrRegistryName
}

resource analyticsIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: identityName
  location: location
}

// Granted here, independently of any resource that will later use this
// identity to pull the analytics-ingest image — the grant exists and is
// active before any Container Apps Job referencing this identity is ever
// created.
resource analyticsIdentityAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acrRegistryId, analyticsIdentity.name, 'AcrPull')
  scope: acrRegistry
  properties: {
    principalId: analyticsIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '7f951dda-4ed3-4680-a7ca-43fe172d538d'
    )
  }
}

output identityId string = analyticsIdentity.id
output identityPrincipalId string = analyticsIdentity.properties.principalId
output identityClientId string = analyticsIdentity.properties.clientId
