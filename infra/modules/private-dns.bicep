// Canvas Marketing OS — private-dns.bicep
// Private DNS zones for Postgres, Key Vault, and Blob Storage private
// endpoints, each linked to the VNet so in-VNet resolution works.

@description('Resource id of the VNet to link each private DNS zone to.')
param vnetId string

var postgresZoneName = 'privatelink.postgres.database.azure.com'
var vaultZoneName = 'privatelink.vaultcore.azure.net'
var blobZoneName = 'privatelink.blob.core.windows.net'

resource postgresZone 'Microsoft.Network/privateDnsZones@2020-06-01' = {
  name: postgresZoneName
  location: 'global'
}

resource vaultZone 'Microsoft.Network/privateDnsZones@2020-06-01' = {
  name: vaultZoneName
  location: 'global'
}

resource blobZone 'Microsoft.Network/privateDnsZones@2020-06-01' = {
  name: blobZoneName
  location: 'global'
}

resource postgresZoneLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: postgresZone
  name: 'link-vnet-cmos-dev'
  location: 'global'
  properties: {
    virtualNetwork: {
      id: vnetId
    }
    registrationEnabled: false
  }
}

resource vaultZoneLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: vaultZone
  name: 'link-vnet-cmos-dev'
  location: 'global'
  properties: {
    virtualNetwork: {
      id: vnetId
    }
    registrationEnabled: false
  }
}

resource blobZoneLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: blobZone
  name: 'link-vnet-cmos-dev'
  location: 'global'
  properties: {
    virtualNetwork: {
      id: vnetId
    }
    registrationEnabled: false
  }
}

output postgresZoneId string = postgresZone.id
output vaultZoneId string = vaultZone.id
output blobZoneId string = blobZone.id
