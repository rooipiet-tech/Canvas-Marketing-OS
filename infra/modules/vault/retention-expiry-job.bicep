// Canvas Marketing OS — infra/modules/vault/retention-expiry-job.bicep
//
// caj-vault-retention-expiry — a one-shot Container Apps Job running the
// Vault service's own image with `python -m vault.retention` as its
// entrypoint (services/vault/vault/retention.py's run_retention_expiry(),
// the same shared function the HTTP POST /retention-expiry-runs path
// calls — AC-007, AC-009). Same job mechanism as migration-job.bicep/
// vault-query-job.bicep, pulling from the shared ACR — never an admin
// username/password.
//
// PATCH: pulls its image via the pre-provisioned USER-ASSIGNED
// managed identity (managed-identity.bicep's id-vault), not a
// system-assigned identity — see that file's header for the confirmed
// live chicken-and-egg ordering bug this avoids (a system-assigned
// identity's AcrPull grant can only be created after this Job exists,
// but the Job needs to pull its image, via that same grant, to be
// created). Still carries a system-assigned identity for the Storage
// Blob Data Contributor grant below, since that's a runtime data-plane
// read, not image-pull-at-creation-time, and isn't subject to the same
// ordering bug.

@description('Azure region.')
param location string = resourceGroup().location

@description('Container Apps Job name.')
param jobName string = 'caj-vault-retention-expiry'

@description('Resource id of the Container Apps managed environment (cae-cmos-dev).')
param environmentId string

@description('Postgres server fully-qualified domain name.')
param postgresFqdn string

@description('Postgres administrator login.')
param administratorLogin string

@secure()
@description('Postgres administrator login password.')
param administratorLoginPassword string

@description('Postgres database name the Vault service connects to.')
param databaseName string = 'postgres'

@description('Shared ACR login server (infra/modules/container-registry.bicep output).')
param acrLoginServer string

@description('Shared ACR resource name, for the AcrPull role assignment scope.')
param acrRegistryName string

@description('Resource id of the shared ACR (informational — see managed-identity.bicep for the AcrPull grant).')
param acrRegistryId string

@description('Resource id of the pre-provisioned user-assigned managed identity (managed-identity.bicep\'s id-vault), used to pull vaultImage from acrLoginServer without the system-assigned-identity ordering bug.')
param userAssignedIdentityId string

@description('Vault service image reference, e.g. <acrLoginServer>/vault:<tag>.')
param vaultImage string

@description('Storage account name backing content-addressed asset blobs.')
param storageAccountName string

@description('Resource id of the storage account, for the Storage Blob Data Contributor role assignment scope.')
param storageAccountId string

@description('Blob container name for content-addressed Vault assets.')
param blobContainerName string = 'vault-assets'

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-01-01' existing = {
  name: storageAccountName
}

var databaseUrl = 'postgresql://${administratorLogin}:${administratorLoginPassword}@${postgresFqdn}:5432/${databaseName}?sslmode=require'

resource retentionExpiryJob 'Microsoft.App/jobs@2024-03-01' = {
  name: jobName
  location: location
  identity: {
    type: 'SystemAssigned, UserAssigned'
    userAssignedIdentities: {
      '${userAssignedIdentityId}': {}
    }
  }
  tags: {
    purpose: 'retention-class expiry sweep across every Vault object type'
  }
  properties: {
    environmentId: environmentId
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: 900
      replicaRetryLimit: 1
      manualTriggerConfig: {
        replicaCompletionCount: 1
        parallelism: 1
      }
      secrets: [
        {
          name: 'db-connection-string'
          value: databaseUrl
        }
      ]
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
          name: 'vault-retention-expiry'
          image: vaultImage
          command: ['python', '-m', 'vault.retention']
          env: [
            {
              name: 'DATABASE_URL'
              secretRef: 'db-connection-string'
            }
            {
              name: 'STORAGE_ACCOUNT_NAME'
              value: storageAccountName
            }
            {
              name: 'BLOB_CONTAINER_NAME'
              value: blobContainerName
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

// No AcrPull role assignment here — retentionExpiryJob pulls its image
// via userAssignedIdentityId, which managed-identity.bicep already
// grants AcrPull to, independently and ahead of this resource.

resource retentionJobBlobContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccountId, retentionExpiryJob.name, 'Storage Blob Data Contributor')
  scope: storageAccount
  properties: {
    principalId: retentionExpiryJob.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
    )
  }
}

output jobName string = retentionExpiryJob.name
output jobId string = retentionExpiryJob.id
