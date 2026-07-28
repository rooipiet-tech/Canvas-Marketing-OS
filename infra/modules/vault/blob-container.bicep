// Canvas Marketing OS — infra/modules/vault/blob-container.bicep
//
// References the existing shared storage account (infra/modules/storage.bicep,
// untouched by this module) and adds one new blob container, 'vault-assets',
// for content-addressed asset storage (vault/storage.py). No new storage
// account, no account key — ca-vault authenticates via its managed
// identity, granted Storage Blob Data Contributor scoped to this account
// (infra/modules/vault/container-app.bicep).

@description('Name of the existing shared storage account (infra/modules/storage.bicep output).')
param storageAccountName string

@description('Blob container name for content-addressed Vault assets.')
param containerName string = 'vault-assets'

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-01-01' existing = {
  name: storageAccountName
}

resource blobServices 'Microsoft.Storage/storageAccounts/blobServices@2023-01-01' existing = {
  parent: storageAccount
  name: 'default'
}

resource vaultAssetsContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-01-01' = {
  parent: blobServices
  name: containerName
  properties: {
    publicAccess: 'None'
  }
}

output containerName string = vaultAssetsContainer.name
output storageAccountId string = storageAccount.id
