// Canvas Marketing OS — infra/modules/vault/retention-expiry-job.bicep
//
// caj-vault-retention-expiry — a one-shot Container Apps Job running the
// Vault service's own image with `python -m vault.retention` as its
// entrypoint (services/vault/vault/retention.py's run_retention_expiry(),
// the same shared function the HTTP POST /retention-expiry-runs path
// calls — AC-007, AC-009). Same job mechanism as migration-job.bicep/
// vault-query-job.bicep, pulling from the shared ACR via managed
// identity (AcrPull), never an admin username/password.

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

@description('Resource id of the shared ACR (informational — role assignment scope is resolved from acrRegistryName).')
param acrRegistryId string

@description('Vault service image reference, e.g. <acrLoginServer>/vault:<tag>.')
param vaultImage string

@description('Storage account name backing content-addressed asset blobs.')
param storageAccountName string

@description('Resource id of the storage account, for the Storage Blob Data Contributor role assignment scope.')
param storageAccountId string

@description('Blob container name for content-addressed Vault assets.')
param blobContainerName string = 'vault-assets'

resource acrRegistry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: acrRegistryName
}

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-01-01' existing = {
  name: storageAccountName
}

var databaseUrl = 'postgresql://${administratorLogin}:${administratorLoginPassword}@${postgresFqdn}:5432/${databaseName}?sslmode=require'

resource retentionExpiryJob 'Microsoft.App/jobs@2024-03-01' = {
  name: jobName
  location: location
  identity: {
    type: 'SystemAssigned'
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
          identity: 'system'
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

resource retentionJobAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acrRegistryId, retentionExpiryJob.name, 'AcrPull')
  scope: acrRegistry
  properties: {
    principalId: retentionExpiryJob.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '7f951dda-4ed3-4680-a7ca-43fe172d538d'
    )
  }
}

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
