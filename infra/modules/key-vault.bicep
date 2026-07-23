// Canvas Marketing OS — key-vault.bicep
// Key Vault with networkAcls.defaultAction Deny, reachable via a private
// endpoint in snet-pe. Deployed for future Wave-2+ secrets (INFRA-007);
// NOT in the critical path for the Postgres migration credential — no
// dependsOn relationship to postgres.bicep (they deploy in parallel).

@description('Azure region.')
param location string = resourceGroup().location

@description('Base name used to derive a globally-unique Key Vault name.')
param namePrefix string = 'kv-cmos-dev'

@description('Tenant id for the vault access policies.')
param tenantId string = subscription().tenantId

@description('Resource id of the subnet private endpoints are created in.')
param privateEndpointSubnetId string

@description('Resource id of the privatelink.vaultcore.azure.net private DNS zone.')
param vaultPrivateDnsZoneId string

var vaultName = '${namePrefix}-${uniqueString(resourceGroup().id)}'

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: take(vaultName, 24)
  location: location
  properties: {
    sku: {
      family: 'A'
      name: 'standard'
    }
    tenantId: tenantId
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    networkAcls: {
      defaultAction: 'Deny'
      bypass: 'AzureServices'
    }
    publicNetworkAccess: 'Disabled'
  }
}

resource keyVaultPrivateEndpoint 'Microsoft.Network/privateEndpoints@2023-09-01' = {
  name: 'pe-${take(vaultName, 24)}'
  location: location
  properties: {
    subnet: {
      id: privateEndpointSubnetId
    }
    privateLinkServiceConnections: [
      {
        name: 'pe-${take(vaultName, 24)}-connection'
        properties: {
          privateLinkServiceId: keyVault.id
          groupIds: [
            'vault'
          ]
        }
      }
    ]
  }
}

resource keyVaultPrivateDnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-09-01' = {
  parent: keyVaultPrivateEndpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'vault-config'
        properties: {
          privateDnsZoneId: vaultPrivateDnsZoneId
        }
      }
    ]
  }
}

output vaultName string = keyVault.name
output vaultId string = keyVault.id
