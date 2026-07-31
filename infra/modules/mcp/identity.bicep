// Canvas Marketing OS — infra/modules/mcp/identity.bicep
//
// Generic user-assigned managed identity, instantiated once per mcp-*
// Container App (id-mcp-web, id-mcp-buffer, id-mcp-canva) rather than a
// single shared identity, for ACR-pull/blast-radius separation between
// the three servers (plan v3, F5-REGRESSION fix: all three identities,
// including mcp-web's, get the Key Vault Secrets User role assignment —
// see key-vault-role-assignment.bicep — even though mcp-web itself never
// calls Key Vault; this is an accepted, harmless, unused residual grant
// per AC-11's literal "every mcp-* Container App's identity" wording).

@description('Azure region.')
param location string = resourceGroup().location

@description('User-assigned managed identity name, e.g. id-mcp-web.')
param identityName string

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: identityName
  location: location
}

output identityId string = identity.id
output principalId string = identity.properties.principalId
output clientId string = identity.properties.clientId
