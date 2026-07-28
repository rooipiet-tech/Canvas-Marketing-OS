// Canvas Marketing OS — infra/modules/vault/managed-identity.bicep
//
// id-vault — a USER-ASSIGNED managed identity shared by every Vault
// Container App / Job that pulls its image from the shared ACR at
// creation time (ca-vault, caj-vault-retention-expiry,
// caj-vault-smoke-test).
//
// PATCH fix for a confirmed live chicken-and-egg ordering bug: a
// SYSTEM-assigned identity's principalId only exists once its owning
// resource (the Container App / Job) has actually been created, so an
// AcrPull role assignment scoped to that principalId can only be
// created AFTER that resource — but the resource needs to pull its
// image from the same ACR (via that same AcrPull grant) in order to be
// created in the first place. Confirmed via
// `az deployment group show -g cmos-dev -n vault-container-app`
// (Failed, "Operation expired" after 20+ minutes) and
// `az role assignment list -g cmos-dev` (zero role assignments existed
// anywhere — the containerApp resource never provisioned, so nothing
// depending on it, including its own AcrPull grant, ever got a chance
// to run).
//
// A user-assigned identity's principalId/resourceId exists
// independently of anything that later references it, so this module
// creates the identity and grants it AcrPull with NO dependency on the
// Container App / Jobs that will use it — it provisions first, and the
// image-pull-time chicken-and-egg is broken.
//
// sidecar-migration-job.bicep (image: postgres:16) and
// secret-writer-job.bicep (image: mcr.microsoft.com/azure-cli:latest)
// do not pull from the shared ACR at all, so they are not subject to
// this ordering bug and are not wired to this identity.

@description('Azure region.')
param location string = resourceGroup().location

@description('User-assigned managed identity name.')
param identityName string = 'id-vault'

@description('Shared ACR resource name, for the AcrPull role assignment scope.')
param acrRegistryName string

@description('Resource id of the shared ACR (informational — role assignment scope is resolved from acrRegistryName).')
param acrRegistryId string

resource acrRegistry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: acrRegistryName
}

resource vaultIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: identityName
  location: location
}

// Granted here, independently of any resource that will later use this
// identity to pull the vault image — this is the whole point of the
// fix: the grant exists and is active before any Container App/Job
// referencing this identity is ever created, so image pull at creation
// time always has a valid AcrPull grant to use.
resource vaultIdentityAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acrRegistryId, vaultIdentity.name, 'AcrPull')
  scope: acrRegistry
  properties: {
    principalId: vaultIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '7f951dda-4ed3-4680-a7ca-43fe172d538d'
    )
  }
}

output identityId string = vaultIdentity.id
output identityPrincipalId string = vaultIdentity.properties.principalId
output identityClientId string = vaultIdentity.properties.clientId
