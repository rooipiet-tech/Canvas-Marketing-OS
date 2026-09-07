// Canvas Marketing OS — infra/modules/cost-management/shutoff-rbac.bicep
//
// Grants la-cost-emergency-shutoff's SystemAssigned identity what it
// actually needs to run its 2 destructive actions: Contributor scoped to
// the Postgres server alone (to call its /stop action) and Contributor
// scoped to each individual Container App alone (to PATCH its scale
// settings) — never resource-group-wide Contributor. There is no built-in
// role narrower than Contributor for "stop this Postgres server"/"patch
// this Container App's scale settings" that this session could verify a
// stable role-definition GUID for live (see this repo's own
// verify_rbac_guids.py convention for why a guessed GUID is worse than a
// broader, scope-narrowed built-in one) — Contributor's blast radius is
// therefore controlled entirely by SCOPE (one resource at a time), not by
// the role's own narrowness.

@description('Postgres Flexible Server name.')
param postgresServerName string

@description('Names of every Container App this identity needs Contributor on.')
param containerAppNames array

@description('principalId of la-cost-emergency-shutoff\'s SystemAssigned identity.')
param principalId string

var contributorRoleId = 'b24988ac-6180-42a0-ab88-20f7382dd24c'

resource postgres 'Microsoft.DBforPostgreSQL/flexibleServers@2023-06-01-preview' existing = {
  name: postgresServerName
}

resource postgresContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(postgres.id, principalId, 'Contributor', 'cost-shutoff')
  scope: postgres
  properties: {
    principalId: principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', contributorRoleId)
  }
}

resource containerApps 'Microsoft.App/containerApps@2024-03-01' existing = [for name in containerAppNames: {
  name: name
}]

resource containerAppContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for (name, i) in containerAppNames: {
  name: guid(containerApps[i].id, principalId, 'Contributor', 'cost-shutoff')
  scope: containerApps[i]
  properties: {
    principalId: principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', contributorRoleId)
  }
}]
