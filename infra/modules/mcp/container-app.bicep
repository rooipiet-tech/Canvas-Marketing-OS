// Canvas Marketing OS — infra/modules/mcp/container-app.bicep
//
// Generic Container App definition, instantiated once per mcp-* server
// (mcp-web, mcp-buffer, mcp-canva). Reuses the existing cae-cmos-dev
// Container Apps Environment (already VNet-integrated per
// container-apps-environment.bicep — AC-10's "if instead an existing CAE
// id is only taken as a param, this sub-check is N/A" applies here, no
// new managedEnvironments resource is declared by this module).
//
// Internal-only ingress (external: false) — mirrors this repo's
// private-endpoint/in-VNet-only posture for anything holding or fetching
// credentials; reachable only from inside cae-cmos-dev's VNet (e.g. by
// caj-mcp-smoke), never from the public internet.
//
// Image pull is via the Container App's own user-assigned identity
// (registries[].identity) — no admin credentials/secretRef for registry
// auth (AC-21). Vendor secrets (BUFFER_API_KEY, CANVA_CLIENT_ID,
// CANVA_CLIENT_SECRET) are wired as Container Apps secrets backed
// directly by a Key Vault reference (secrets[].keyVaultUrl +
// identity), resolved using the same user-assigned identity that holds
// the Key Vault Secrets User role (see key-vault-role-assignment.bicep) —
// mcp-web passes an empty keyVaultSecretRefs array (no vendor secret).

@description('Azure region.')
param location string = resourceGroup().location

@description('Container App name, e.g. mcp-web.')
param appName string

@description('Resource id of the Container Apps managed environment (cae-cmos-dev).')
param environmentId string

@description('Full container image reference, e.g. <acrLoginServer>/mcp-web:latest.')
param image string

@description('Login server of the Azure Container Registry the image is pulled from.')
param registryLoginServer string

@description('Resource id of the user-assigned managed identity this app runs as (also used for ACR pull and Key Vault secretRef resolution).')
param userAssignedIdentityId string

@description('Target port the container listens on.')
param targetPort int = 8080

@description('Plain (non-secret) environment variables: [{ name, value }].')
param envVars array = []

@description('Key-Vault-backed secret environment variables: [{ envName, keyVaultUrl }]. Empty for servers with no vendor credential (e.g. mcp-web).')
param keyVaultSecretRefs array = []

@description('Changes on every deploy (main.bicep defaults it to utcNow()) so this app always gets a NEW revision. Governance-round-4 pattern (see gatekeeper-app.bicep/vault/container-app.bicep/orchestrator/container-app.bicep): with activeRevisionsMode Single, a redeploy that only changes a secret VALUE (e.g. a rotated Postgres admin password, or — for mcp-buffer/mcp-canva — an updated Key Vault secret VERSION) does NOT create a new revision — the already-running replica keeps the secret values it booted with, indefinitely. Forcing a fresh revisionSuffix every deploy is what actually restarts the container and picks up current values.')
param deployToken string

var keyVaultSecrets = [for kv in keyVaultSecretRefs: {
  name: toLower(replace(kv.envName, '_', '-'))
  keyVaultUrl: kv.keyVaultUrl
  identity: userAssignedIdentityId
}]

var keyVaultSecretEnv = [for kv in keyVaultSecretRefs: {
  name: kv.envName
  secretRef: toLower(replace(kv.envName, '_', '-'))
}]

resource containerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: appName
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${userAssignedIdentityId}': {}
    }
  }
  properties: {
    environmentId: environmentId
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: false
        targetPort: targetPort
        transport: 'auto'
      }
      registries: [
        {
          server: registryLoginServer
          identity: userAssignedIdentityId
        }
      ]
      secrets: keyVaultSecrets
    }
    template: {
      containers: [
        {
          name: appName
          image: image
          env: concat(envVars, keyVaultSecretEnv)
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 1
      }
      revisionSuffix: 'r${uniqueString(deployToken)}'
    }
  }
}

output appId string = containerApp.id
output appName string = containerApp.name
output fqdn string = containerApp.properties.configuration.ingress.fqdn
