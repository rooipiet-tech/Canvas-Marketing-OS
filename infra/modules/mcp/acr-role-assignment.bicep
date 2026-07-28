// Canvas Marketing OS — infra/modules/mcp/acr-role-assignment.bicep
//
// Grants a single principal the built-in AcrPull role
// (7f951dda-4ed3-4680-a7ca-43fe172d538d) on the target Azure Container
// Registry, scoped to the registry resource — least privilege, image-pull
// only, no push/delete/admin access. Instantiated 3x from
// infra/main.bicep (mcp-web, mcp-buffer, mcp-canva identities all need to
// pull their own image).

@description('Name of the existing Azure Container Registry.')
param registryName string

@description('Principal id (managed identity) to grant AcrPull to.')
param principalId string

var acrPullRoleId = '7f951dda-4ed3-4680-a7ca-43fe172d538d'

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: registryName
}

resource roleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, principalId, acrPullRoleId)
  scope: registry
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleId)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}

output roleAssignmentId string = roleAssignment.id
