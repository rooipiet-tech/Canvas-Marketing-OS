// Canvas Marketing OS — gateway.bicep
//
// The model-gateway Container App: a UserAssigned managed identity (see
// IDENTITY MODEL below for why not SystemAssigned), internal ingress only
// (matching the frozen contract's canonical server url
// https://model-gateway.internal.cmos.dev), pulling its image from the
// shared ACR with that same identity — no registry admin credential exists.
//
// REGISTRY DEPENDENCY (fix/deploy-infra-gateway — was the root cause of
// deploy-infra #10's "no such host" failure): this module takes
// containerRegistryLoginServer/containerRegistryName as REAL OUTPUT
// REFERENCES from the container-registry module (main.bicep passes
// `containerRegistry.outputs.loginServer` / `.registryName`, never a
// separately-computed string). Referencing a module's output is what gives
// Bicep an implicit dependsOn — without it, ARM had no ordering guarantee
// between the two modules and could (and did) start provisioning this
// Container App before the registry resource, and its DNS record, existed
// at all. The earlier design avoided this on purpose to dodge a *different*
// problem — container-registry.bicep's AcrPull grant used to take this
// app's principalId as an input, which would have made the two modules
// depend on each other in both directions (a genuine cycle Bicep refuses to
// compile). That's why the AcrPull role assignment now lives HERE instead:
// this module pulls in the registry as an `existing` resource (see below,
// same pattern as the Key Vault reference) and grants its own identity pull
// rights directly, so container-registry.bicep never needs to know this
// app's principalId and the cycle never exists in the first place.
//
// KEY VAULT ACCESS (L-0011): a Key Vault deployed with
// enableRbacAuthorization: true grants NO data-plane access to anyone by
// default — not even a subscription Owner. The Container App's native Key
// Vault secret reference below therefore only works because of the explicit
// "Key Vault Secrets User" role assignment further down this file. This is
// the first Microsoft.Authorization/roleAssignments resource anywhere in
// infra/; do not remove it assuming control-plane rights are enough.
//
// VERIFICATION PATH (L-0012, C5): the vault's publicNetworkAccess stays
// Disabled and this app's ingress stays internal, so nothing on a
// GitHub-hosted runner can reach either. The post-deploy smoke test runs
// in-VNet instead, as a one-shot Container Apps Job (caj-gateway-smoke)
// mirroring the existing caj-vault-query pattern — CI only triggers it via
// the Azure control plane and polls for the result. See
// .github/workflows/deploy-gateway.yml.
//
// BOOTSTRAP IMAGE (fix/deploy-infra-gateway): a brand-new environment has no
// image in the shared ACR yet — deploy-gateway.yml hasn't run. `containerImage`
// therefore defaults to a public MCR quickstart image that needs no
// authentication and no dependency on our own registry at all, so the very
// first `az deployment group create` can succeed end to end. On every deploy
// after the first, deploy-infra.yml's preflight reads this app's CURRENT live
// image via `az containerapp show` and passes that same value back in as the
// `gatewayContainerImage` parameter — but ONLY if the app has ever produced a
// ready revision (`latestReadyRevisionName` non-empty); otherwise it falls
// back to the placeholder too, so a first deploy that failed partway through
// never poisons every subsequent run with an image reference that has never
// actually worked (this exact trap is what caused the round-2 failure below).
// deploy-gateway.yml remains the ONLY thing that ever sets a real gateway
// image, via `az containerapp update --image ...` — this module never builds
// an image reference itself.
//
// IDENTITY MODEL (fix/deploy-infra-gateway round 2): confirmed via live
// diagnosis (`az deployment operation group list` + the Container App's
// event stream) that the previous SystemAssigned-identity design deadlocks
// on first deploy. Both role assignments below used to read
// `gatewayApp.identity.principalId` — a property Bicep can only resolve once
// gatewayApp's OWN deployment operation reaches a terminal state. But for
// Container Apps, that terminal state is not reached until the first
// revision goes healthy, which requires AcrPull (to pull any private-ACR
// image) and Key Vault Secrets User (to resolve the anthropic-api-key secret
// reference before the container can even start) to already be granted. The
// live symptom matched exactly: the app sat in `provisioningState: Failed`
// ("Operation expired") for 20+ minutes, its event stream showed repeating
// ACR token-exchange 401s, and `az role assignment list --assignee
// <principalId>` came back empty — the role-assignment resources never got a
// chance to deploy at all. Fix: a UserAssignedIdentity (below) is a plain,
// synchronously-available resource with no such LRO — its principalId is
// known the instant it's created, so both role assignments now target it and
// complete BEFORE gatewayApp is created (see gatewayApp's explicit
// `dependsOn`, which is genuine here since gatewayApp's params don't
// reference either role assignment's outputs). gatewayApp is then assigned
// that identity via `identity.userAssignedIdentities`, and both the registry
// pull and the Key Vault secret reference use its resource id instead of the
// literal `'system'`.

@description('Azure region.')
param location string = resourceGroup().location

@description('Container App name.')
param appName string = 'ca-model-gateway'

@description('Resource id of the Container Apps managed environment (cae-cmos-dev).')
param environmentId string

@description('Key Vault NAME (not resource id) holding the upstream provider API key.')
param keyVaultName string

@description('Postgres server fully-qualified domain name.')
param postgresFqdn string

@description('Postgres administrator login.')
param administratorLogin string

@secure()
@description('Postgres administrator login password.')
param administratorLoginPassword string

@description('Postgres database name holding the Vault schema.')
param databaseName string = 'postgres'

@description('ACR login server — the container-registry module\'s real `loginServer` OUTPUT, so this module has a genuine (implicit) dependency on it and ARM always provisions the registry first.')
param containerRegistryLoginServer string

@description('ACR resource name — the container-registry module\'s real `registryName` OUTPUT, used below to look up the registry as an `existing` resource for this app\'s own AcrPull grant.')
param containerRegistryName string

@description('Full container image reference to deploy (registry/repo:tag). Defaults to a public, unauthenticated MCR quickstart image so a brand-new environment can bootstrap before any real image has ever been pushed. deploy-infra.yml\'s preflight overrides this with the app\'s current live image on every run after the first, so a routine infra redeploy never regresses a real image back to this placeholder; deploy-gateway.yml is the only thing that ever sets a real one, via `az containerapp update --image`.')
param containerImage string = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

@description('Name of the Key Vault secret holding the upstream provider API key.')
param anthropicSecretName string = 'anthropic-api-key'

// F-GATEWAY-SECRET-STALE-REVISION (6 Aug 2026, round 19i): same
// governance-round-4 pattern as governanceDeployToken/vaultDeployToken/
// orchestratorDeployToken/mcpDeployToken in main.bicep — this app runs
// activeRevisionsMode Single (the Container Apps default, unset below), so
// a redeploy that only changes a secret VALUE (administratorLoginPassword
// rotates on every deploy-governance/deploy-infra run) does NOT create a
// new revision on its own — the already-running replica keeps the
// DATABASE_URL it booted with, indefinitely, even after the live Postgres
// password has changed underneath it. This module was the one app in the
// governance-round-4 sweep that never received a deploy token, which is
// exactly why two consecutive real heartbeat runs (#64, #65) failed with
// "FATAL: password authentication failed for user cmosadmin" from
// model-gateway specifically, while every other service was unaffected.
// Forcing a fresh revisionSuffix every deploy is what actually restarts
// the container and picks up the current secret value.
@description('Deployment-time token threaded into ca-model-gateway to force a fresh Container Apps revision each deploy, same pattern/reasoning as vaultDeployToken. Defaults to utcNow() in main.bicep, evaluated once per `az deployment group create`/`what-if` run.')
param deployToken string

// Same connection-string convention as migration-job.bicep / vault-query-job.bicep.
var databaseUrl = 'postgresql://${administratorLogin}:${administratorLoginPassword}@${postgresFqdn}:5432/${databaseName}?sslmode=require'
var vaultUri = 'https://${keyVaultName}.vault.azure.net/'

resource kv 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

// `existing` reference to the shared ACR, purely so this app can grant
// itself AcrPull without container-registry.bicep ever needing to know this
// app's principalId (see the header comment on why that matters).
resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: containerRegistryName
}

// UserAssigned identity — see the header comment's IDENTITY MODEL section
// for why this replaced SystemAssigned. Its principalId is available the
// instant this resource is created, with no dependency on gatewayApp at all.
resource gatewayIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${appName}-identity'
  location: location
}

// Key Vault Secrets User (4633458b-17de-408a-b874-0445c86b69e6) for the
// gateway's identity, scoped to the vault. Without this, the secret
// reference below resolves to a Forbidden error at revision start.
resource kvSecretsUserRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(kv.id, gatewayIdentity.id, 'kv-secrets-user')
  scope: kv
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
    principalId: gatewayIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// AcrPull (7f951dda-4ed3-4680-a7ca-43fe172d538d) for the gateway's own
// identity, scoped to the shared ACR — granted HERE rather than via
// container-registry.bicep's generic pullPrincipalId mechanism specifically
// so the registry module never needs this app's principalId as an input
// (see the header comment). Real image pulls need this; the bootstrap
// placeholder image is public and doesn't.
resource acrPullRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, gatewayIdentity.id, 'acr-pull')
  scope: acr
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
    principalId: gatewayIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource gatewayApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: appName
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${gatewayIdentity.id}': {}
    }
  }
  // Both role assignments must be active before the first revision tries to
  // pull the image / resolve the Key Vault secret — neither is referenced by
  // this resource's params, so Bicep would not otherwise infer the ordering.
  // This is a genuine, necessary dependsOn (see main.bicep's DEPENDSON
  // POLICY comment), not one the linter would flag.
  dependsOn: [
    kvSecretsUserRoleAssignment
    acrPullRoleAssignment
  ]
  tags: {
    purpose: 'model gateway (internal ingress only)'
  }
  properties: {
    environmentId: environmentId
    configuration: {
      // Explicit (matches the Container Apps default) so the
      // F-GATEWAY-SECRET-STALE-REVISION reasoning above and the
      // revisionSuffix below are both legible at the call site, same as
      // vault/container-app.bicep's activeRevisionsMode: 'Single'.
      activeRevisionsMode: 'Single'
      ingress: {
        external: false
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false
      }
      secrets: [
        {
          name: 'db-connection-string'
          value: databaseUrl
        }
        {
          // Container Apps' native Key Vault secret reference, resolved with
          // this app's UserAssigned identity — see kvSecretsUserRoleAssignment above.
          name: 'anthropic-api-key'
          keyVaultUrl: '${vaultUri}secrets/${anthropicSecretName}'
          identity: gatewayIdentity.id
        }
      ]
      registries: [
        {
          server: containerRegistryLoginServer
          identity: gatewayIdentity.id
        }
      ]
    }
    template: {
      // Forces a fresh revision every deploy so a rotated
      // administratorLoginPassword actually takes effect — see the
      // F-GATEWAY-SECRET-STALE-REVISION comment on the deployToken param
      // above. Same 'r${uniqueString(deployToken)}' convention as
      // vault/container-app.bicep.
      revisionSuffix: 'r${uniqueString(deployToken)}'
      containers: [
        {
          name: 'model-gateway'
          image: containerImage
          env: [
            {
              name: 'DATABASE_URL'
              secretRef: 'db-connection-string'
            }
            {
              name: 'ANTHROPIC_API_KEY'
              secretRef: 'anthropic-api-key'
            }
            {
              name: 'KEY_VAULT_NAME'
              value: keyVaultName
            }
            {
              name: 'DELIBERATE_FLAG_ENABLED'
              value: 'false'
            }
          ]
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 3
      }
    }
  }
}

output appName string = gatewayApp.name
output appId string = gatewayApp.id
output principalId string = gatewayIdentity.properties.principalId
output fqdn string = gatewayApp.properties.configuration.ingress.fqdn
