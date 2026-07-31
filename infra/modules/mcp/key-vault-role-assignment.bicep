// Canvas Marketing OS — infra/modules/mcp/key-vault-role-assignment.bicep
//
// Grants a single principal the built-in Key Vault Secrets User role
// (4633458b-17de-408a-b874-0445c86b69e6) on the target Key Vault, scoped
// to the vault resource — least privilege, per L-0011/AC-11. Never Owner
// (8e3af657-a8ff-443c-a75c-2fe8c4bcb635) or Contributor
// (b24988ac-6180-42a0-ab88-20f7382dd24c).
//
// Instantiated 3x from infra/main.bicep (mcp-web, mcp-buffer, mcp-canva
// identities all get this role — plan v3 F5-REGRESSION fix — even though
// mcp-web's identity never actually reads a secret; AC-11's frozen text
// requires "every mcp-* Container App's identity" to hold this grant).

@description('Name of the existing Key Vault to grant access on.')
param keyVaultName string

@description('Principal id (managed identity) to grant Key Vault Secrets User to.')
param principalId string

var keyVaultSecretsUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6'

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

resource roleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, principalId, keyVaultSecretsUserRoleId)
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', keyVaultSecretsUserRoleId)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}

output roleAssignmentId string = roleAssignment.id
