// Canvas Marketing OS — infra/modules/analytics/main.bicep
//
// Aggregator module for the analytics-ingest service: managed identity,
// the analytics-fabric-export blob container, the schema migration job,
// the nightly Schedule-triggered ingestion job, and the gated one-shot
// Buffer introspection smoke job. Every cross-service value below arrives
// as a param passed down from infra/main.bicep — this module never calls
// loadTextContent itself and never computes a resourceGroup()/
// subscription()-derived string duplicating another module's naming
// convention (AC-31).
//
// Role assignments: id-analytics gets Key Vault Secrets User (buffer-api-key
// / ga4-service-account-key / search-console-service-account-key /
// linkedin-analytics-client-secret fallback resolution, per
// analytics_ingest.credentials) and Storage Blob Data Contributor (the
// nightly Fabric export upload) — both granted independently of any Job
// resource, same L-0020 ordering-safe pattern as
// infra/modules/vault/container-app.bicep's identical grants.

@description('Azure region.')
param location string = resourceGroup().location

@description('Resource id of the Container Apps managed environment (cae-cmos-dev).')
param environmentId string

@description('Postgres server fully-qualified domain name.')
param postgresFqdn string

@description('Postgres administrator login.')
param administratorLogin string

@secure()
@description('Postgres administrator login password.')
param administratorLoginPassword string

@secure()
@description('Full contents of services/analytics-ingest/migrations/0001_analytics_init.sql, loaded by main.bicep via loadTextContent.')
param migrationSql string

@description('Key Vault name (infra/modules/key-vault.bicep output).')
param keyVaultName string

@description('Resource id of the Key Vault, for role assignment scope.')
param keyVaultId string

@description('Shared storage account name (infra/modules/storage.bicep output).')
param storageAccountName string

@description('Resource id of the shared storage account, for role assignment scope.')
param storageAccountId string

@description('Shared ACR resource name (infra/modules/container-registry.bicep output).')
param acrRegistryName string

@description('Resource id of the shared ACR.')
param acrRegistryId string

@description('Vault service internal base URL (e.g. https://<ca-vault internalFqdn>) — analytics_ingest.vault_client.VAULT_API_URL.')
param vaultApiBaseUrl string

@description('analytics-ingest container image reference for caj-analytics-nightly-ingest. Defaults to a public MCR placeholder — see nightly-ingest-job.bicep\'s header.')
param nightlyIngestContainerImage string = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

@description('analytics-ingest container image reference for caj-analytics-buffer-smoke. Defaults to a public MCR placeholder — see buffer-smoke-job.bicep\'s header.')
param bufferSmokeContainerImage string = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-01-01' existing = {
  name: storageAccountName
}

// A plain compile-time-known literal (never a module output) — deliberately
// used inline in the two roleAssignments' `name: guid(...)` expressions
// below instead of managedIdentity.outputs.identityId/identityPrincipalId,
// which Bicep rejects there (BCP120: a role assignment's `name` must be
// calculable at the START of deployment; a module output is only known
// once that module has actually deployed). Mirrors
// infra/modules/vault/managed-identity.bicep's own `vaultIdentity.name`
// usage in its AcrPull roleAssignment's `name:` for the identical reason.
var analyticsIdentityName = 'id-analytics'

module managedIdentity 'managed-identity.bicep' = {
  name: 'analytics-managed-identity'
  params: {
    location: location
    identityName: analyticsIdentityName
    acrRegistryName: acrRegistryName
    acrRegistryId: acrRegistryId
  }
}

module blobContainer 'blob-container.bicep' = {
  name: 'analytics-blob-container'
  params: {
    storageAccountName: storageAccountName
  }
}

module migrationJob 'migration-job.bicep' = {
  name: 'analytics-migration-job'
  params: {
    location: location
    environmentId: environmentId
    postgresFqdn: postgresFqdn
    administratorLogin: administratorLogin
    administratorLoginPassword: administratorLoginPassword
    migrationSql: migrationSql
  }
}

module nightlyIngestJob 'nightly-ingest-job.bicep' = {
  name: 'analytics-nightly-ingest-job'
  params: {
    location: location
    environmentId: environmentId
    postgresFqdn: postgresFqdn
    administratorLogin: administratorLogin
    administratorLoginPassword: administratorLoginPassword
    keyVaultUri: keyVault.properties.vaultUri
    vaultApiUrl: vaultApiBaseUrl
    storageAccountName: storageAccountName
    image: nightlyIngestContainerImage
  }
  dependsOn: [
    blobContainer
  ]
}

module bufferSmokeJob 'buffer-smoke-job.bicep' = {
  name: 'analytics-buffer-smoke-job'
  params: {
    location: location
    environmentId: environmentId
    keyVaultUri: keyVault.properties.vaultUri
    image: bufferSmokeContainerImage
  }
}

// Granted independently of any Job resource — no ordering deadlock
// (L-0020). id-analytics is attached to caj-analytics-nightly-ingest /
// caj-analytics-buffer-smoke exclusively via CLI post-create
// (analytics-image.yml), so these grants are already active by the time
// either job ever actually runs.
resource analyticsKvSecretsUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVaultId, analyticsIdentityName, 'Key Vault Secrets User')
  scope: keyVault
  properties: {
    principalId: managedIdentity.outputs.identityPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '4633458b-17de-408a-b874-0445c86b69e6'
    )
  }
}

resource analyticsBlobContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccountId, analyticsIdentityName, 'Storage Blob Data Contributor')
  scope: storageAccount
  properties: {
    principalId: managedIdentity.outputs.identityPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
    )
  }
}

output managedIdentityId string = managedIdentity.outputs.identityId
output migrationJobName string = migrationJob.outputs.jobName
output nightlyIngestJobName string = nightlyIngestJob.outputs.jobName
output bufferSmokeJobName string = bufferSmokeJob.outputs.jobName
output blobContainerName string = blobContainer.outputs.containerName
