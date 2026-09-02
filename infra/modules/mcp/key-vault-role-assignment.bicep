// Canvas Marketing OS — infra/modules/mcp/key-vault-role-assignment.bicep
//
// Grants a single principal the built-in Key Vault Secrets User role
// (4633458b-17de-408a-b874-0445c86b69e6) on the target Key Vault. Never
// Owner (8e3af657-a8ff-443c-a75c-2fe8c4bcb635) or Contributor
// (b24988ac-6180-42a0-ab88-20f7382dd24c).
//
// NOT least privilege, despite what this header claimed until 2026-09-02.
// `scope: keyVault` below is the WHOLE vault, so a holder can read every
// secret in it, not the one or two it is wired to. Key Vault RBAC does
// support assignment at `/secrets/<name>` scope; narrowing each consumer
// to the secrets its own keyVaultSecretRefs names is finding 1 of the
// 01-security-and-data audit (issue #135) and is deliberately a separate
// change — a wrong secret name here crash-loops a service inside the VNet,
// so it wants its own deploy with someone watching.
//
// Instantiated 2x from infra/main.bicep, for mcp-buffer and mcp-canva.
// mcp-web is not one of them: it reads no secret, and its grant was
// removed with the same audit finding — see main.bicep's comment there.

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
