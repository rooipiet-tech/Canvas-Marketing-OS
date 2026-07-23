// Canvas Marketing OS — postgres.bicep
// Postgres Flexible Server 16, public network access Disabled, reachable
// only via a private endpoint in snet-pe. Password-based admin auth
// (administratorLoginPassword @secure(), no default — supplied at deploy
// time by the workflow; no Key Vault round-trip in this credential's
// critical path). No dependsOn relationship to key-vault.bicep.

@description('Azure region.')
param location string = resourceGroup().location

@description('Postgres flexible server name.')
param serverName string = 'psql-cmos-dev'

@description('Non-secret administrator login name.')
param administratorLogin string = 'cmosadmin'

@secure()
@description('Administrator login password. No default — must be supplied at deploy time.')
param administratorLoginPassword string

@description('Resource id of the subnet private endpoints are created in.')
param privateEndpointSubnetId string

@description('Resource id of the privatelink.postgres.database.azure.com private DNS zone.')
param postgresPrivateDnsZoneId string

resource postgres 'Microsoft.DBforPostgreSQL/flexibleServers@2023-06-01-preview' = {
  name: serverName
  location: location
  sku: {
    name: 'Standard_B1ms'
    tier: 'Burstable'
  }
  properties: {
    version: '16'
    administratorLogin: administratorLogin
    administratorLoginPassword: administratorLoginPassword
    storage: {
      storageSizeGB: 32
    }
    backup: {
      backupRetentionDays: 7
      geoRedundantBackup: 'Disabled'
    }
    network: {
      publicNetworkAccess: 'Disabled'
    }
    availabilityZone: '1'
  }
}

resource postgresPrivateEndpoint 'Microsoft.Network/privateEndpoints@2023-09-01' = {
  name: 'pe-${serverName}'
  location: location
  properties: {
    subnet: {
      id: privateEndpointSubnetId
    }
    privateLinkServiceConnections: [
      {
        name: 'pe-${serverName}-connection'
        properties: {
          privateLinkServiceId: postgres.id
          groupIds: [
            'postgresqlServer'
          ]
        }
      }
    ]
  }
}

resource postgresPrivateDnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-09-01' = {
  parent: postgresPrivateEndpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'postgres-config'
        properties: {
          privateDnsZoneId: postgresPrivateDnsZoneId
        }
      }
    ]
  }
}

output serverName string = postgres.name
output serverId string = postgres.id
output fqdn string = postgres.properties.fullyQualifiedDomainName
