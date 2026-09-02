// Canvas Marketing OS — infra/modules/mcp/identity.bicep
//
// Generic user-assigned managed identity, instantiated once per mcp-*
// Container App (id-mcp-web, id-mcp-buffer, id-mcp-canva) rather than a
// single shared identity, for ACR-pull/blast-radius separation between
// the three servers. mcp-buffer's and mcp-canva's identities hold the Key
// Vault Secrets User role assignment (see key-vault-role-assignment.bicep);
// mcp-web's does NOT, because mcp-web calls no credential path and is
// wired to no secret. It held one until 2026-09-02 as "an accepted,
// harmless, unused residual" — removed with finding 1 of the
// 01-security-and-data audit, issue #135, since mcp-web is the component
// that ingests untrusted web content and so the worst holder of it.

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
