// Canvas Marketing OS — infra/modules/analytics/blob-container.bicep
//
// References the existing shared storage account (infra/modules/storage.bicep,
// untouched by this module) and adds one new blob container,
// 'analytics-fabric-export', for the nightly Fabric shortcut export
// (analytics_ingest.blob_writer). No new storage account, no account key
// — caj-analytics-nightly-ingest authenticates via its managed identity,
// granted Storage Blob Data Contributor scoped to this account
// (infra/modules/analytics/main.bicep). Mirrors
// infra/modules/vault/blob-container.bicep exactly.

@description('Name of the existing shared storage account (infra/modules/storage.bicep output).')
param storageAccountName string

@description('Blob container name for the nightly Fabric shortcut export.')
param containerName string = 'analytics-fabric-export'

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-01-01' existing = {
  name: storageAccountName
}

resource blobServices 'Microsoft.Storage/storageAccounts/blobServices@2023-01-01' existing = {
  parent: storageAccount
  name: 'default'
}

resource analyticsFabricExportContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-01-01' = {
  parent: blobServices
  name: containerName
  properties: {
    publicAccess: 'None'
  }
}

output containerName string = analyticsFabricExportContainer.name
output storageAccountId string = storageAccount.id
