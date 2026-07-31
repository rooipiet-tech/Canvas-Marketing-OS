// Canvas Marketing OS — infra/modules/vault/container-app.bicep
//
// ca-vault — the Vault service Container App. Internal ingress only
// (VNet-integrated into cae-cmos-dev, reaches Postgres exclusively over
// its private endpoint — AC-012). Pulls its image from the single
// shared platform ACR via a pre-provisioned USER-ASSIGNED managed
// identity (managed-identity.bicep's id-vault) — never the ACR admin
// account, and never a system-assigned identity for the pull itself:
// see managed-identity.bicep's header for the chicken-and-egg ordering
// bug (confirmed live) that a system-assigned identity's AcrPull grant
// hits at Container App creation time, which this sidesteps. Also
// carries a SYSTEM-assigned identity for the Key Vault Secrets User /
// Storage Blob Data Contributor grants below — those are runtime data-
// plane reads (DB connection string at startup, blobs on request), not
// image-pull-at-creation-time, so they aren't subject to the same
// ordering bug and can stay on the simpler system-assigned identity.
// Reads its DB connection string from Key Vault at runtime via
// vault/db.py's Key Vault fallback, using the explicit Key Vault
// Secrets User role assignment below (AC-010, L-0011) — never a client
// secret, never publicNetworkAccess opened on the vault (L-0012; the
// secret itself is loaded by secret-writer-job.bicep's in-VNet job, not
// by this app).

@description('Azure region.')
param location string = resourceGroup().location

@description('Container App name.')
param appName string = 'ca-vault'

@description('Resource id of the Container Apps managed environment (cae-cmos-dev).')
param environmentId string

@description('Shared ACR login server (infra/modules/container-registry.bicep output).')
param acrLoginServer string

@description('Shared ACR resource name (informational — kept for interface parity with the other vault child modules; the AcrPull grant for image pull lives on managed-identity.bicep\'s identity, not scoped here).')
param acrRegistryName string

@description('Resource id of the shared ACR (informational — see acrRegistryName).')
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

@description('Key Vault name holding the Vault DB connection secret.')
param keyVaultName string

@description('Resource id of the Key Vault (informational — role assignment scope is resolved from keyVaultName).')
param keyVaultId string

@description('Key Vault secret name for the Vault DB connection string.')
param dbConnectionSecretName string = 'vault-db-connection-string'

@description('Changes on every deploy (main.bicep defaults it to utcNow()) so this app always gets a NEW revision. Same governance-round-4 pattern as gatekeeper-app.bicep/publisher-app.bicep: with activeRevisionsMode Single, a redeploy that only changes a secret VALUE (e.g. a rotated Postgres admin password) does NOT create a new revision — the already-running replica keeps the DATABASE_URL it booted with, indefinitely, even after the live password has changed underneath it. Forcing a fresh revisionSuffix every deploy is what actually restarts the container and picks up the current secret values.')
param deployToken string

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-01-01' existing = {
  name: storageAccountName
}

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

resource vaultApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: appName
  location: location
  identity: {
    type: 'SystemAssigned, UserAssigned'
    userAssignedIdentities: {
      '${userAssignedIdentityId}': {}
    }
  }
  properties: {
    environmentId: environmentId
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: false
        targetPort: 8000
        transport: 'auto'
      }
      registries: [
        {
          server: acrLoginServer
          identity: userAssignedIdentityId
        }
      ]
    }
    template: {
      revisionSuffix: 'r${uniqueString(deployToken)}'
      containers: [
        {
          name: 'vault'
          image: vaultImage
          env: [
            {
              name: 'KEY_VAULT_NAME'
              value: keyVaultName
            }
            {
              name: 'DB_CONNECTION_SECRET_NAME'
              value: dbConnectionSecretName
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
          probes: [
            {
              type: 'Readiness'
              httpGet: {
                path: '/health'
                port: 8000
              }
              initialDelaySeconds: 5
              periodSeconds: 10
            }
            {
              type: 'Liveness'
              httpGet: {
                path: '/health'
                port: 8000
              }
              initialDelaySeconds: 10
              periodSeconds: 30
            }
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 3
      }
    }
  }
}

// AC-010 / L-0011: RBAC-mode Key Vault grants no data-plane access by
// default, not even to the resource owner — ca-vault's identity needs
// this explicit role assignment to read vault-db-connection-string.
resource vaultKvSecretsUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVaultId, vaultApp.name, 'Key Vault Secrets User')
  scope: keyVault
  properties: {
    principalId: vaultApp.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '4633458b-17de-408a-b874-0445c86b69e6'
    )
  }
}

resource vaultBlobContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccountId, vaultApp.name, 'Storage Blob Data Contributor')
  scope: storageAccount
  properties: {
    principalId: vaultApp.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
    )
  }
}

// No AcrPull role assignment here — vaultApp pulls its image via
// userAssignedIdentityId, which managed-identity.bicep already grants
// AcrPull to, independently and ahead of this resource. See this file's
// header and managed-identity.bicep's header for the ordering bug this
// avoids.

output appName string = vaultApp.name
output appId string = vaultApp.id
output internalFqdn string = vaultApp.properties.configuration.ingress.fqdn
output principalId string = vaultApp.identity.principalId
