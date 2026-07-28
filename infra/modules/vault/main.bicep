// Canvas Marketing OS — infra/modules/vault/main.bicep
//
// Vault service entry point, orchestrating 6 child modules:
//   blob-container, sidecar-migration-job, secret-writer-job,
//   container-app, retention-expiry-job, smoke-test-job.
//
// administratorLoginPassword flows down from infra/main.bicep's existing
// top-level secure param exactly the way it already flows to
// migration-job.bicep / vault-query-job.bicep (plan v2 F3) — no new
// credential-handling pattern is introduced here.

@description('Azure region.')
param location string = resourceGroup().location

@description('Resource id of the Container Apps managed environment (cae-cmos-dev).')
param environmentId string

@description('Postgres server fully-qualified domain name.')
param postgresFqdn string

@description('Postgres administrator login.')
param administratorLogin string

@secure()
@description('Postgres administrator login password — flows from infra/main.bicep, same as migrationJob/vaultQueryJob.')
param administratorLoginPassword string

@description('Postgres database name the Vault service connects to.')
param databaseName string = 'postgres'

@secure()
@description('Full contents of services/vault/migrations/0001_vault_internal_init.sql, loaded by infra/main.bicep via loadTextContent.')
param migrationSql string

@description('Key Vault name holding the Vault DB connection secret.')
param keyVaultName string

@description('Resource id of the Key Vault.')
param keyVaultId string

@description('Storage account name backing content-addressed asset blobs.')
param storageAccountName string

@description('Resource id of the storage account.')
param storageAccountId string

@description('Shared ACR login server (infra/modules/container-registry.bicep output).')
param acrLoginServer string

@description('Shared ACR resource name.')
param acrRegistryName string

@description('Resource id of the shared ACR.')
param acrRegistryId string

@description('Vault service image tag to deploy (e.g. a commit SHA pushed by .github/workflows/vault-image.yml).')
param vaultImageTag string = 'latest'

@description('Deployment-time token threaded into ca-vault to force a fresh Container Apps revision each deploy — see infra/main.bicep\'s vaultDeployToken and container-app.bicep\'s deployToken param for the full reasoning (same governance-round-4 pattern as gatekeeper-app.bicep/publisher-app.bicep).')
param deployToken string

var vaultImage = '${acrLoginServer}/vault:${vaultImageTag}'
var blobContainerName = 'vault-assets'

module blobContainer 'blob-container.bicep' = {
  name: 'vault-blob-container'
  params: {
    storageAccountName: storageAccountName
    containerName: blobContainerName
  }
}

module sidecarMigrationJob 'sidecar-migration-job.bicep' = {
  name: 'vault-sidecar-migration-job'
  params: {
    location: location
    environmentId: environmentId
    postgresFqdn: postgresFqdn
    administratorLogin: administratorLogin
    administratorLoginPassword: administratorLoginPassword
    databaseName: databaseName
    migrationSql: migrationSql
  }
}

module secretWriterJob 'secret-writer-job.bicep' = {
  name: 'vault-secret-writer-job'
  params: {
    location: location
    environmentId: environmentId
    postgresFqdn: postgresFqdn
    administratorLogin: administratorLogin
    administratorLoginPassword: administratorLoginPassword
    databaseName: databaseName
    keyVaultName: keyVaultName
    keyVaultId: keyVaultId
  }
}

module containerApp 'container-app.bicep' = {
  name: 'vault-container-app'
  params: {
    location: location
    environmentId: environmentId
    acrLoginServer: acrLoginServer
    acrRegistryName: acrRegistryName
    acrRegistryId: acrRegistryId
    vaultImage: vaultImage
    storageAccountName: storageAccountName
    storageAccountId: storageAccountId
    blobContainerName: blobContainerName
    keyVaultName: keyVaultName
    keyVaultId: keyVaultId
    deployToken: deployToken
  }
  dependsOn: [
    blobContainer
    secretWriterJob
  ]
}

module retentionExpiryJob 'retention-expiry-job.bicep' = {
  name: 'vault-retention-expiry-job'
  params: {
    location: location
    environmentId: environmentId
    postgresFqdn: postgresFqdn
    administratorLogin: administratorLogin
    administratorLoginPassword: administratorLoginPassword
    databaseName: databaseName
    acrLoginServer: acrLoginServer
    acrRegistryName: acrRegistryName
    acrRegistryId: acrRegistryId
    vaultImage: vaultImage
    storageAccountName: storageAccountName
    storageAccountId: storageAccountId
    blobContainerName: blobContainerName
  }
}

module smokeTestJob 'smoke-test-job.bicep' = {
  name: 'vault-smoke-test-job'
  params: {
    location: location
    environmentId: environmentId
    postgresFqdn: postgresFqdn
    administratorLogin: administratorLogin
    administratorLoginPassword: administratorLoginPassword
    databaseName: databaseName
    acrLoginServer: acrLoginServer
    acrRegistryName: acrRegistryName
    acrRegistryId: acrRegistryId
    vaultImage: vaultImage
    vaultBaseUrl: 'https://${containerApp.outputs.internalFqdn}'
  }
  dependsOn: [
    containerApp
  ]
}

output containerAppName string = containerApp.outputs.appName
output containerAppInternalFqdn string = containerApp.outputs.internalFqdn
output blobContainerName string = blobContainer.outputs.containerName
output sidecarMigrationJobName string = sidecarMigrationJob.outputs.jobName
output secretWriterJobName string = secretWriterJob.outputs.jobName
output retentionExpiryJobName string = retentionExpiryJob.outputs.jobName
output smokeTestJobName string = smokeTestJob.outputs.jobName
