// Canvas Marketing OS — app-insights.bicep
//
// A new, workspace-based Application Insights resource (INFRA-004),
// linked to the EXISTING log-cmos-dev Log Analytics workspace (created in
// container-apps-environment.bicep — no new Log Analytics workspace is
// created here).
//
// REGIONAL RESIDENCY (C-1, verified via Microsoft Learn's Application
// Insights FAQ this session): data is stored in the region the resource
// was created in ONLY if the region-specific connection string is used —
// the global endpoint does not guarantee regional compliance. `location`
// is therefore pinned to the literal string 'southafricanorth', NOT
// resourceGroup().location, so this guarantee holds even if a future
// deploy parameterizes location differently for some other module.

@description('Azure region — pinned explicitly to southafricanorth (C-1 regional residency), never resourceGroup().location.')
param location string = 'southafricanorth'

@description('Application Insights resource name.')
param appInsightsName string = 'appi-console-cmos-dev'

@description('Resource id of the existing log-cmos-dev Log Analytics workspace (container-apps-environment.bicep output) — no new workspace is created here.')
param logAnalyticsWorkspaceId string

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalyticsWorkspaceId
    IngestionMode: 'LogAnalytics'
  }
}

output connectionString string = appInsights.properties.ConnectionString
output appId string = appInsights.properties.AppId
output name string = appInsights.name
