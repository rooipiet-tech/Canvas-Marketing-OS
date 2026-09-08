// Canvas Marketing OS — infra/modules/vault/main.bicep
//
// Vault service entry point, orchestrating 8 child modules:
//   managed-identity, blob-container, sidecar-migration-job,
//   options-inbox-migration-job, secret-writer-job, container-app,
//   retention-expiry-job, smoke-test-job.
//
// administratorLoginPassword flows down from infra/main.bicep's existing
// top-level secure param exactly the way it already flows to
// migration-job.bicep / vault-query-job.bicep (plan v2 F3) — no new
// credential-handling pattern is introduced here.
//
// PATCH: managedIdentity is created first, independently (no
// dependsOn), and its output resource id is threaded into containerApp,
// retentionExpiryJob and smokeTestJob — the 3 child modules whose
// resource pulls the shared-ACR vault image at creation time. See
// managed-identity.bicep's header for the confirmed live
// system-assigned-identity ordering bug this fixes.
// sidecarMigrationJob (image: postgres:16) and secretWriterJob (image:
// mcr.microsoft.com/azure-cli:latest) don't pull the ACR image and are
// left unchanged.

@description('Azure region.')
param location string = resourceGroup().location

@description('Resource id of the Container Apps managed environment (cae-cmos-dev).')
param environmentId string

@description('Postgres server fully-qualified domain name. Used by every child job EXCEPT secretWriterJob (see pgbouncerFqdn below) — those are one-shot DDL/admin jobs, out of scope for TD-12\'s PgBouncer routing.')
param postgresFqdn string

@description('TD-12: ca-pgbouncer\'s internal FQDN. Used ONLY by secretWriterJob — the job whose write into Key Vault (vault-db-connection-string) is what ca-vault itself actually connects with at runtime (vault/db.py\'s Key Vault fallback). Routing just this one job through PgBouncer, rather than threading it through containerApp, is what actually changes ca-vault\'s live connection target; see secret-writer-job.bicep\'s header.')
param pgbouncerFqdn string

@description('TD-12: port ca-pgbouncer listens on — see pgbouncerFqdn above.')
param pgbouncerPort int = 6432

@description('Postgres administrator login.')
param administratorLogin string

@secure()
@description('Postgres administrator login password — flows from infra/main.bicep, same as migrationJob/vaultQueryJob.')
param administratorLoginPassword string

@description('Postgres database name the Vault service connects to.')
param databaseName string = 'postgres'

@description('Shared migration-ledger runner script (infra/modules/migration-ledger-runner.sh, TD-15), loaded by infra/main.bicep via loadTextContent and passed straight through to sidecarMigrationJob and optionsInboxMigrationJob.')
param migrationRunnerScript string

@secure()
@description('Bundle of {version, sql} pairs for services/vault/migrations/0001_vault_internal_init.sql, built by infra/main.bicep. See infra/modules/migration-ledger-runner.sh for the wire format.')
param migrationBundleBase64 string

@secure()
@description('Bundle of {version, sql} pairs for services/vault/migrations/000{2,3}_*.sql (Appendix D PR 1/3), built by infra/main.bicep. See infra/modules/migration-ledger-runner.sh for the wire format.')
param optionsInboxMigrationBundleBase64 string

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

@secure()
@minLength(1)
@description('TD-03: shared-secret bearer token flowing from infra/main.bicep\'s vaultApiToken param — threaded to containerApp (validates it) and smokeTestJob (sends it).')
param apiToken string

@description('Deployment-time token threaded into ca-vault to force a fresh Container Apps revision each deploy — see infra/main.bicep\'s vaultDeployToken and container-app.bicep\'s deployToken param for the full reasoning (same governance-round-4 pattern as gatekeeper-app.bicep/publisher-app.bicep).')
param deployToken string

var vaultImage = '${acrLoginServer}/vault:${vaultImageTag}'
var blobContainerName = 'vault-assets'

module managedIdentity 'managed-identity.bicep' = {
  name: 'vault-managed-identity'
  params: {
    location: location
    acrRegistryName: acrRegistryName
    acrRegistryId: acrRegistryId
  }
}

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
    runnerScript: migrationRunnerScript
    migrationBundleBase64: migrationBundleBase64
  }
}

module optionsInboxMigrationJob 'options-inbox-migration-job.bicep' = {
  name: 'vault-options-inbox-migration-job'
  params: {
    location: location
    environmentId: environmentId
    postgresFqdn: postgresFqdn
    administratorLogin: administratorLogin
    administratorLoginPassword: administratorLoginPassword
    databaseName: databaseName
    runnerScript: migrationRunnerScript
    migrationBundleBase64: optionsInboxMigrationBundleBase64
  }
}

module secretWriterJob 'secret-writer-job.bicep' = {
  name: 'vault-secret-writer-job'
  params: {
    location: location
    environmentId: environmentId
    // TD-12: pgbouncerFqdn/pgbouncerPort, NOT postgresFqdn — see this
    // module's header and secret-writer-job.bicep's own param comment.
    postgresFqdn: pgbouncerFqdn
    postgresPort: pgbouncerPort
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
    userAssignedIdentityId: managedIdentity.outputs.identityId
    vaultImage: vaultImage
    storageAccountName: storageAccountName
    storageAccountId: storageAccountId
    blobContainerName: blobContainerName
    keyVaultName: keyVaultName
    keyVaultId: keyVaultId
    apiToken: apiToken
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
    userAssignedIdentityId: managedIdentity.outputs.identityId
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
    userAssignedIdentityId: managedIdentity.outputs.identityId
    vaultImage: vaultImage
    vaultBaseUrl: 'https://${containerApp.outputs.internalFqdn}'
    apiToken: apiToken
  }
  dependsOn: [
    containerApp
  ]
}

output managedIdentityId string = managedIdentity.outputs.identityId
output containerAppName string = containerApp.outputs.appName
output containerAppInternalFqdn string = containerApp.outputs.internalFqdn
output blobContainerName string = blobContainer.outputs.containerName
output sidecarMigrationJobName string = sidecarMigrationJob.outputs.jobName
output optionsInboxMigrationJobName string = optionsInboxMigrationJob.outputs.jobName
output secretWriterJobName string = secretWriterJob.outputs.jobName
output retentionExpiryJobName string = retentionExpiryJob.outputs.jobName
output smokeTestJobName string = smokeTestJob.outputs.jobName
