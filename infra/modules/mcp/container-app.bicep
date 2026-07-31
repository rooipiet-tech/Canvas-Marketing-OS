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
// MAIDEN-DEPLOY INCIDENT (L-0049 class, 2026-07-31): this module used to
// declare `registries[]` (ACR pull via this app's own user-assigned
// identity) directly in the initial create. That combination — a
// user-assigned identity newly attached via `identity.userAssignedIdentities`
// AND referenced by `registries[].identity` for ACR pull auth in the SAME
// initial `Microsoft.App/containerApps` create call — is a confirmed,
// documented, unresolved Azure Container Apps platform limitation
// (microsoft/azure-container-apps#1467; see ca-console's identical
// maiden-deploy IdentityDoesNotExist failure and infra/modules/console/
// console-app.bicep's header for the full incident writeup). This module
// therefore never declares `registries[]` at all — deploy-mcp.yml's `az
// containerapp registry set` (Microsoft's own documented reliable
// workaround) owns the ACR-pull identity exclusively, run every deploy
// before the image update — mirroring console-app.bicep exactly.
// `image` therefore also follows the L-0048 3-part bootstrap contract:
// main.bicep's mcp{Web,Buffer,Canva}ContainerImage params default to a
// public MCR placeholder needing no registry auth at all; deploy-infra.yml's
// preflight resolves each to the app's CURRENT live image once one exists;
// only deploy-mcp.yml's gated deploy job (via `az containerapp update
// --image`) ever sets a real, SHA-pinned image.
//
// Vendor secrets (BUFFER_API_KEY, CANVA_CLIENT_ID, CANVA_CLIENT_SECRET) are
// still wired as Container Apps secrets backed directly by a Key Vault
// reference (secrets[].keyVaultUrl + identity), resolved using the same
// user-assigned identity that holds the Key Vault Secrets User role (see
// key-vault-role-assignment.bicep) — mcp-web passes an empty
// keyVaultSecretRefs array (no vendor secret). This is a different Azure
// subsystem (Key Vault secret-reference resolution, not ACR-pull
// credential resolution) from the registries[] issue above, and has not
// been observed to hit the same platform limitation.

@description('Azure region.')
param location string = resourceGroup().location

@description('Container App name, e.g. mcp-web.')
param appName string

@description('Resource id of the Container Apps managed environment (cae-cmos-dev).')
param environmentId string

@description('Full container image reference. Defaults to a public MCR placeholder at the call site (main.bicep) — see the MAIDEN-DEPLOY INCIDENT header comment above. Only deploy-mcp.yml (via `az containerapp update --image`, after `az containerapp registry set`) ever sets a real, SHA-pinned image.')
param image string

@description('Resource id of the user-assigned managed identity this app runs as (used for Key Vault secretRef resolution; ACR pull is attached exclusively by deploy-mcp.yml via `az containerapp registry set`, never by this template — see the header comment above).')
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
      // Deliberately NO registries[] block — see the MAIDEN-DEPLOY
      // INCIDENT comment on the `image` param above. deploy-mcp.yml's `az
      // containerapp registry set` owns this exclusively once the app
      // exists; declaring it here would fight that CLI step on every
      // subsequent idempotent `az deployment group create` re-apply (an
      // omitted property is left untouched by an incremental deployment;
      // an explicit empty array here would wipe it instead).
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
