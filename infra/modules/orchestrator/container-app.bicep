// Canvas Marketing OS — infra/modules/orchestrator/container-app.bicep
//
// ca-orchestrator — the orchestrator service Container App. Dual identity
// (SystemAssigned, UserAssigned), internal ingress only (exposes /status +
// /health). Pulls its image from the single shared platform ACR via the
// pre-provisioned id-orchestrator USER-assigned identity (managed-identity.bicep),
// never the ACR admin account, never a second registry (C11) — see that
// module's header for the chicken-and-egg AcrPull ordering bug (L-0020,
// confirmed live for ca-vault) a system-assigned identity's own AcrPull
// grant hits at Container App creation time, which this sidesteps. Reads
// DATABASE_URL from a Container Apps secret built the same way
// migration-job.bicep builds databaseUrl. Talks to the existing Service Bus
// namespace via the SYSTEM-assigned identity only (never a connection
// string/SAS key, C4/disableLocalAuth=true) — granted both "Azure Service
// Bus Data Sender" and "Azure Service Bus Data Receiver" below (AC-024),
// since this app both publishes task envelopes and consumes the
// event/task queues (worker.py). These are runtime-only data-plane reads,
// not image-pull-at-creation-time, so they aren't subject to the same
// ordering bug and can stay on the simpler system-assigned identity —
// same split infra/modules/vault/container-app.bicep uses.
//
// F-TEAMS-NEEDS-EDIT-WEBHOOK (11 Aug 2026): the live Container App
// (pre-existing, out-of-band) already carries CMOS_GATEWAY_BASE_URL,
// CMOS_MCP_WEB_BASE_URL and CMOS_GATEKEEPER_BASE_URL — confirmed via `az
// containerapp revision list` that none of the three were ever declared
// here; they were added directly against the running resource, outside
// this template, at some point during the campaign. Backported here so
// this file is the true source of infra state again — a future
// deploy-infra run without this backport would have silently reverted
// ca-orchestrator's ability to reach the model gateway, mcp-web and
// gatekeeper. Reuses the SAME `teams-webhook-url` Key Vault secret
// gatekeeper-app.bicep already resolves (see that file's identical
// param), so orchestrator.teams_notify.notify_needs_edit() (Proposal C,
// currently a confirmed no-op — see
// claude_qa-block-retry-investigation-2026-08-11.md) starts actually
// posting to Teams the moment this deploys, with zero code change and no
// new Teams connector.
//
// F-TEAMS-WEBHOOK-KV-CHICKEN-EGG-FIX (11 Aug 2026): the first version of
// the change above referenced the teams-webhook-url secret with
// `identity: 'system'` and granted Key Vault Secrets User to
// orchestratorApp.identity.principalId (the SYSTEM-assigned identity).
// That recreates the EXACT ordering bug this file's own header already
// warns about for ACR (L-0020), just for Key Vault instead: a
// system-assigned identity's principalId only exists once orchestratorApp
// itself has been created, so the role assignment can only run AFTER
// orchestratorApp — but orchestratorApp's own provisioning now needs to
// resolve teams-webhook-url via that identity to build its secrets block
// in the first place. Confirmed live: deploy-infra run #126 (11 Aug 2026)
// failed with "Unable to get value using Managed identity system for
// secret teams-webhook-url" — orchestratorKeyVaultSecretsUser was never
// even reached because it depends on orchestratorApp, which had already
// failed. Fixed the same way managed-identity.bicep fixes it for ACR:
// use the pre-provisioned USER-assigned identity (userAssignedIdentityId)
// for the secret reference instead of 'system', and grant Key Vault
// Secrets User to that identity's principalId (passed in as
// userAssignedIdentityPrincipalId, a plain param with no dependency on
// orchestratorApp) so the grant exists and is active independently of,
// and before, orchestratorApp is ever created — exactly the registries[]
// block above already does for AcrPull.

@description('Azure region.')
param location string = resourceGroup().location

@description('Container App name.')
param appName string = 'ca-orchestrator'

@description('Resource id of the Container Apps managed environment (cae-cmos-dev).')
param environmentId string

@description('Shared ACR login server (infra/modules/container-registry.bicep output).')
param acrLoginServer string

@description('Shared ACR resource name (informational — kept for interface parity with the other orchestrator child modules; the AcrPull grant for image pull lives on managed-identity.bicep\'s identity, not scoped here).')
param acrRegistryName string

@description('Resource id of the shared ACR (informational — see acrRegistryName).')
param acrRegistryId string

@description('Resource id of the pre-provisioned user-assigned managed identity (managed-identity.bicep\'s id-orchestrator), used to pull orchestratorImage from acrLoginServer without the system-assigned-identity ordering bug.')
param userAssignedIdentityId string

@description('principalId of the SAME pre-provisioned user-assigned identity as userAssignedIdentityId (managed-identity.bicep\'s outputs.identityPrincipalId). Exists independently of orchestratorApp, so it can be granted Key Vault access before orchestratorApp is ever created — see F-TEAMS-WEBHOOK-KV-CHICKEN-EGG-FIX above.')
param userAssignedIdentityPrincipalId string

@description('Orchestrator service image reference, e.g. <acrLoginServer>/orchestrator:<tag>.')
param orchestratorImage string

@description('Postgres server fully-qualified domain name.')
param postgresFqdn string

@description('Postgres administrator login.')
param administratorLogin string

@secure()
@description('Postgres administrator login password.')
param administratorLoginPassword string

@description('Postgres database name to connect to.')
param databaseName string = 'postgres'

@description('Best-effort Vault API base URL (may be unreachable — orchestrator/vault_client.py degrades gracefully per AC-016/AC-017).')
param vaultApiUrl string = ''

@description('Existing Service Bus namespace name (infra/modules/service-bus.bicep output).')
param serviceBusNamespaceName string

@description('Model-gateway internal base URL (ca-model-gateway). F-TEAMS-NEEDS-EDIT-WEBHOOK backport — was live-only, undeclared here, before 11 Aug 2026.')
param cmosGatewayBaseUrl string = ''

@description('mcp-web internal base URL. F-TEAMS-NEEDS-EDIT-WEBHOOK backport — was live-only, undeclared here, before 11 Aug 2026.')
param cmosMcpWebBaseUrl string = ''

@description('ca-gatekeeper internal base URL. F-TEAMS-NEEDS-EDIT-WEBHOOK backport — was live-only, undeclared here, before 11 Aug 2026.')
param cmosGatekeeperBaseUrl string = ''

@description('Console internal base URL, for teams_notify.py\'s "Review in console" Action.OpenUrl button on the Teams needs-edit/retry-exhausted cards (F-TEAMS-CARD-REVIEW-LINK). Constructed in main.bicep from the shared Container Apps environment\'s defaultDomain, NOT from ca-console\'s own module output — see main.bicep\'s comment for why (avoids a circular module dependency with console-app.bicep\'s own orchestratorApiBaseUrl param).')
param cmosConsoleBaseUrl string = ''

@description('Name of the existing Key Vault (infra/modules/key-vault.bicep output), for the teams-webhook-url Secrets User role assignment below.')
param keyVaultName string

@description('Key Vault URL of the teams-webhook-url secret — the SAME secret infra/modules/governance/gatekeeper-app.bicep already resolves (see that file\'s teamsWebhookUrlKeyVaultUrl param). Resolved via the pre-provisioned user-assigned identity (userAssignedIdentityId), granted Key Vault Secrets User below — see F-TEAMS-WEBHOOK-KV-CHICKEN-EGG-FIX.')
param teamsWebhookUrlKeyVaultUrl string

@description('Changes on every deploy (main.bicep defaults it to utcNow()) so this app always gets a NEW revision. Same governance-round-4 pattern as vault/container-app.bicep: with activeRevisionsMode Single, a redeploy that only changes a secret VALUE (e.g. a rotated Postgres admin password) does NOT create a new revision — the already-running replica keeps the DATABASE_URL it booted with, indefinitely, even after the live password has changed underneath it. Forcing a fresh revisionSuffix every deploy is what actually restarts the container and picks up the current secret values.')
param deployToken string

var databaseUrl = 'postgresql://${administratorLogin}:${administratorLoginPassword}@${postgresFqdn}:5432/${databaseName}?sslmode=require'

// F5: identical resource type/API version to infra/modules/service-bus.bicep,
// same serviceBusNamespaceName param name/pattern container-app.bicep uses.
resource serviceBusNamespace 'Microsoft.ServiceBus/namespaces@2022-10-01-preview' existing = {
  name: serviceBusNamespaceName
}

// F-TEAMS-NEEDS-EDIT-WEBHOOK: existing lookup only (never created here) —
// same pattern signing-key.bicep uses for the same vault.
resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

resource orchestratorApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: appName
  location: location
  identity: {
    type: 'SystemAssigned, UserAssigned'
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
        targetPort: 8000
        transport: 'auto'
      }
      registries: [
        {
          server: acrLoginServer
          identity: userAssignedIdentityId
        }
      ]
      secrets: [
        {
          name: 'database-url'
          value: databaseUrl
        }
        {
          name: 'teams-webhook-url'
          keyVaultUrl: teamsWebhookUrlKeyVaultUrl
          identity: userAssignedIdentityId
        }
      ]
    }
    template: {
      revisionSuffix: 'r${uniqueString(deployToken)}'
      containers: [
        {
          name: 'orchestrator'
          image: orchestratorImage
          env: [
            {
              name: 'DATABASE_URL'
              secretRef: 'database-url'
            }
            {
              name: 'VAULT_API_URL'
              value: vaultApiUrl
            }
            {
              name: 'SERVICE_BUS_NAMESPACE'
              value: '${serviceBusNamespaceName}.servicebus.windows.net'
            }
            {
              name: 'CMOS_GATEWAY_BASE_URL'
              value: cmosGatewayBaseUrl
            }
            {
              name: 'CMOS_MCP_WEB_BASE_URL'
              value: cmosMcpWebBaseUrl
            }
            {
              name: 'CMOS_GATEKEEPER_BASE_URL'
              value: cmosGatekeeperBaseUrl
            }
            {
              name: 'CMOS_CONSOLE_BASE_URL'
              value: cmosConsoleBaseUrl
            }
            {
              name: 'TEAMS_WEBHOOK_URL'
              secretRef: 'teams-webhook-url'
            }
          ]
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          probes: [
            {
              type: 'Readiness'
              httpGet: {
                path: '/health'
                port: 8000
              }
              initialDelaySeconds: 5
              periodSeconds: 10
            }
            {
              type: 'Liveness'
              httpGet: {
                path: '/health'
                port: 8000
              }
              initialDelaySeconds: 10
              periodSeconds: 30
            }
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 3
      }
    }
  }
}

// No AcrPull role assignment here — orchestratorApp pulls its image via
// userAssignedIdentityId, which managed-identity.bicep already grants
// AcrPull to, independently and ahead of this resource. See this file's
// header and managed-identity.bicep's header for the ordering bug this
// avoids (L-0020).

// AC-024: Azure Service Bus Data Sender — GUID resolved live via
// services/orchestrator/scripts/verify_rbac_guids.py against this
// subscription's role definitions (see that script's docstring, F2).
resource orchestratorServiceBusDataSender 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(serviceBusNamespace.id, orchestratorApp.name, 'Azure Service Bus Data Sender')
  scope: serviceBusNamespace
  properties: {
    principalId: orchestratorApp.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '69a216fc-b8fb-44d8-bc22-1f3c2cd27a39'
    )
  }
}

// AC-024: Azure Service Bus Data Receiver — GUID resolved live via
// services/orchestrator/scripts/verify_rbac_guids.py against this
// subscription's role definitions (see that script's docstring, F2).
resource orchestratorServiceBusDataReceiver 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(serviceBusNamespace.id, orchestratorApp.name, 'Azure Service Bus Data Receiver')
  scope: serviceBusNamespace
  properties: {
    principalId: orchestratorApp.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '4f6d3b9b-027b-4f4c-9142-0e5a2a2247e0'
    )
  }
}

// F-TEAMS-WEBHOOK-KV-CHICKEN-EGG-FIX: Key Vault Secrets User, scoped to
// just this vault, granted to the pre-provisioned USER-assigned identity
// (userAssignedIdentityPrincipalId) — NOT orchestratorApp's SYSTEM-assigned
// identity. This exists and is active independently of, and before,
// orchestratorApp is ever created (same as orchestratorIdentityAcrPull in
// managed-identity.bicep), which is what breaks the ordering bug described
// in this file's header. Same role definition id
// (4633458b-17de-408a-b874-0445c86b69e6) and same read-only-secrets-only
// scope signing-key.bicep's gatekeeperSecretsUserAssignment grants
// id-cmos-gatekeeper. Least-privilege: read-only, this vault only, no
// crypto/sign permissions (orchestrator never touches the gate-token
// signing key). guid() is seeded from userAssignedIdentityId (a param, not
// a resource reference) so this resource has no implicit dependency on
// orchestratorApp at all.
resource orchestratorKeyVaultSecretsUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, userAssignedIdentityId, 'Key Vault Secrets User')
  scope: keyVault
  properties: {
    principalId: userAssignedIdentityPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '4633458b-17de-408a-b874-0445c86b69e6'
    )
  }
}

output appName string = orchestratorApp.name
output appId string = orchestratorApp.id
output internalFqdn string = orchestratorApp.properties.configuration.ingress.fqdn
output principalId string = orchestratorApp.identity.principalId
