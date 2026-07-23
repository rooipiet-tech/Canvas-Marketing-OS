// Canvas Marketing OS — container-apps-environment.bicep
// Managed Environment cae-cmos-dev, VNet-integrated (infrastructure
// subnet), deliberately a workload-profile environment (workloadProfiles
// array present) so it can host the private-endpoint-reachable Container
// Apps Jobs (migration + query) and future internal-ingress services.

@description('Azure region.')
param location string = resourceGroup().location

@description('Container Apps environment name.')
param environmentName string = 'cae-cmos-dev'

@description('Resource id of the infrastructure subnet the environment is injected into.')
param infrastructureSubnetId string

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-cmos-dev'
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

resource cae 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: environmentName
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
    vnetConfiguration: {
      infrastructureSubnetId: infrastructureSubnetId
      internal: false
    }
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
      {
        name: 'D4'
        workloadProfileType: 'D4'
        minimumCount: 0
        maximumCount: 1
      }
    ]
  }
}

output environmentId string = cae.id
output environmentName string = cae.name
output logAnalyticsWorkspaceId string = logAnalytics.id
