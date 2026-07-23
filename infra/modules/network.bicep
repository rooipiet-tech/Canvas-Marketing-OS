// Canvas Marketing OS — network.bicep
// VNet + subnets: one delegated to Microsoft.App/environments for the
// Container Apps environment infrastructure subnet, one plain subnet for
// private endpoints (Postgres, Key Vault, Storage).

@description('Azure region for all resources in this module.')
param location string = resourceGroup().location

@description('Name of the VNet.')
param vnetName string = 'vnet-cmos-dev'

@description('VNet address space.')
param vnetAddressPrefix string = '10.20.0.0/16'

@description('Container Apps environment infrastructure subnet address prefix.')
param caeInfraSubnetPrefix string = '10.20.0.0/23'

@description('Private endpoints subnet address prefix.')
param peSubnetPrefix string = '10.20.2.0/27'

var caeInfraSubnetName = 'snet-cae-infra'
var peSubnetName = 'snet-pe'

resource vnet 'Microsoft.Network/virtualNetworks@2023-09-01' = {
  name: vnetName
  location: location
  properties: {
    addressSpace: {
      addressPrefixes: [
        vnetAddressPrefix
      ]
    }
    subnets: [
      {
        name: caeInfraSubnetName
        properties: {
          addressPrefix: caeInfraSubnetPrefix
          delegations: [
            {
              name: 'delegation-app-environments'
              properties: {
                serviceName: 'Microsoft.App/environments'
              }
            }
          ]
        }
      }
      {
        name: peSubnetName
        properties: {
          addressPrefix: peSubnetPrefix
          privateEndpointNetworkPolicies: 'Disabled'
        }
      }
    ]
  }
}

output vnetId string = vnet.id
output vnetName string = vnet.name
// Referenced by name (not array index) so ordering changes in the subnets
// array above can never silently swap which id an output resolves to.
output caeInfraSubnetId string = resourceId('Microsoft.Network/virtualNetworks/subnets', vnetName, caeInfraSubnetName)
output peSubnetId string = resourceId('Microsoft.Network/virtualNetworks/subnets', vnetName, peSubnetName)
