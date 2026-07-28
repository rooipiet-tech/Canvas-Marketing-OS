// Canvas Marketing OS — signing-key.bicep
//
// The gate-token signing key and the least-privilege identities that use
// it (AC-15, AC-20).
//
// KEY MATERIAL NEVER LEAVES THE VAULT
// ----------------------------------
// The private key is CREATED by this template as a
// Microsoft.KeyVault/vaults/keys resource. It is never generated outside
// Key Vault and imported, never exported, and never appears in app
// config, a Container Apps secret, or a log line. Gatekeeper signs by
// calling CryptographyClient.sign() (remote sign, digest in / signature
// out) — see services/gatekeeper/app/signer/keyvault_signer.py.
//
// kty is RSA, not EdDSA: kv-cmos-dev is a standard-tier
// (software-protected) vault, and Azure Key Vault supports RSA and EC
// (P-256/384/521/256K) only — there is no Ed25519 key type at any SKU.
// RS256 is therefore the gate-token algorithm.
//
// RBAC GRANTS NOTHING BY DEFAULT (L-0011)
// ---------------------------------------
// The vault has enableRbacAuthorization: true, which means NO principal —
// not even a subscription Owner — has data-plane access without an
// explicit role assignment. Three are made here, split least-privilege:
//
//   1. Gatekeeper identity -> Key Vault Crypto User   (sign-capable)
//   2. Gatekeeper identity -> Key Vault Secrets User  (read teams-webhook-url etc.)
//   3. Publisher identity  -> custom verify-only role (get key + verify;
//                                                      NO sign, NO secrets)
//
// The verify-only role is a custom roleDefinition because no built-in
// Key Vault role grants verify without also granting sign
// (Key Vault Crypto User includes .../keys/sign/action).

@description('Azure region.')
param location string = resourceGroup().location

@description('Name of the existing Key Vault created by modules/key-vault.bicep.')
param keyVaultName string

@description('Name of the gate-token signing key created inside the vault.')
param signingKeyName string = 'gate-token-signing-key'

@description('RSA key size. 2048 is the minimum permitted by AC-15.')
@allowed([
  2048
  3072
  4096
])
param signingKeySize int = 2048

@description('Name of the user-assigned managed identity Gatekeeper runs as.')
param gatekeeperIdentityName string = 'id-cmos-gatekeeper'

@description('Name of the user-assigned managed identity Publisher runs as.')
param publisherIdentityName string = 'id-cmos-publisher'

// Built-in Azure role definition ids (subscription-scoped, stable GUIDs).
var keyVaultCryptoUserRoleId = '12338af0-0e69-4776-bea7-57ae8d297424'
var keyVaultSecretsUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6'

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

resource gatekeeperIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: gatekeeperIdentityName
  location: location
  tags: {
    purpose: 'gatekeeper gate-token signing identity'
  }
}

resource publisherIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: publisherIdentityName
  location: location
  tags: {
    purpose: 'publisher gate-token verification identity'
  }
}

// The signing key itself. keyOps deliberately lists sign+verify only —
// no wrapKey/unwrapKey/encrypt/decrypt, and no export.
resource gateTokenSigningKey 'Microsoft.KeyVault/vaults/keys@2023-07-01' = {
  parent: keyVault
  name: signingKeyName
  properties: {
    kty: 'RSA'
    keySize: signingKeySize
    keyOps: [
      'sign'
      'verify'
    ]
    attributes: {
      enabled: true
      exportable: false
    }
  }
}

// Custom verify-only role: read the public key and verify a signature.
// Deliberately excludes Microsoft.KeyVault/vaults/keys/sign/action.
resource verifyOnlyRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' = {
  name: guid(keyVault.id, 'cmos-gate-token-verify-only')
  properties: {
    roleName: 'CMOS Gate Token Verify Only (${keyVaultName})'
    description: 'Read a Key Vault key and verify signatures with it. Cannot sign, and cannot read secrets.'
    type: 'CustomRole'
    permissions: [
      {
        actions: []
        notActions: []
        dataActions: [
          'Microsoft.KeyVault/vaults/keys/read'
          'Microsoft.KeyVault/vaults/keys/verify/action'
        ]
        notDataActions: []
      }
    ]
    assignableScopes: [
      keyVault.id
    ]
  }
}

// (1) Gatekeeper: sign-capable.
resource gatekeeperCryptoUserAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, gatekeeperIdentity.id, keyVaultCryptoUserRoleId)
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      keyVaultCryptoUserRoleId
    )
    principalId: gatekeeperIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// (2) Gatekeeper: Secrets User, for teams-webhook-url and friends.
resource gatekeeperSecretsUserAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, gatekeeperIdentity.id, keyVaultSecretsUserRoleId)
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      keyVaultSecretsUserRoleId
    )
    principalId: gatekeeperIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// (3) Publisher: verify/get only. No sign. No secrets.
resource publisherVerifyOnlyAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, publisherIdentity.id, verifyOnlyRole.id)
  scope: keyVault
  properties: {
    roleDefinitionId: verifyOnlyRole.id
    principalId: publisherIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

output signingKeyName string = gateTokenSigningKey.name
output signingKeyUri string = gateTokenSigningKey.properties.keyUri
output keyVaultUri string = keyVault.properties.vaultUri
output gatekeeperIdentityId string = gatekeeperIdentity.id
output gatekeeperClientId string = gatekeeperIdentity.properties.clientId
output gatekeeperPrincipalId string = gatekeeperIdentity.properties.principalId
output publisherIdentityId string = publisherIdentity.id
output publisherClientId string = publisherIdentity.properties.clientId
output publisherPrincipalId string = publisherIdentity.properties.principalId
output verifyOnlyRoleDefinitionId string = verifyOnlyRole.id
