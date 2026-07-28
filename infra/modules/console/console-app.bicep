// Canvas Marketing OS — console-app.bicep
//
// The console Container App: externally-reachable ingress (the ONLY
// externally-reachable read surface in the platform, per the auth
// constraint), gated by Entra ID Easy Auth via a Federated Identity
// Credential — fully secretless (L-0013, AUTH-001).
//
// SCHEMA VERIFICATION (performed this session, before authoring this file,
// using ONLY sources that require no live Azure resource access, per the
// no-live-execution ruling):
//   (a) `gh api repos/Azure-Samples/containerapps-builtinauth-bicep/
//       contents/infra/{aca,appupdate,appregistration,main}.bicep` (public
//       GitHub data, not an Azure call) — confirmed the exact resource
//       type `Microsoft.App/containerApps/authConfigs@2024-10-02-preview`,
//       its property nesting (globalValidation.unauthenticatedClientAction,
//       identityProviders.azureActiveDirectory.registration.{clientId,
//       clientSecretSettingName, openIdIssuer}), AND — critically — that
//       the sentinel string 'override-use-mi-fic-assertion-client-id' is
//       used as the NAME of an ordinary Container App secret whose VALUE
//       is the user-assigned identity's `clientId` (a GUID, never a real
//       credential): see the sample's aca.bicep secrets block
//       (`{ name: 'override-use-mi-fic-assertion-client-id', value:
//       acaIdentity.properties.clientId }`) referenced by
//       appupdate.bicep's `clientSecretSettingName`. Azure's Easy Auth
//       runtime recognizes this exact secret name as a sentinel meaning
//       "use this user-assigned identity's Federated Identity Credential
//       for token exchange instead of a real client secret" — it is never
//       treated as an actual client-secret value.
//   (b) `az bicep list-types --resource-type Microsoft.App/containerApps/
//       authConfigs` was attempted as a secondary local/offline
//       cross-check but the installed az/bicep CLI does not recognize
//       `list-types` as a command in this environment (noted as a
//       self-flag — source (a)'s public sample fetch was the conclusive
//       source here, not (b)).
//
// THREE-PHASE BOOTSTRAP (AUTH-003, F1 fix):
//   Phase 1 — the FIRST deploy creates consoleIdentity (a genuine resource,
//     never a lookup — see console-identity.bicep) and this authConfig with
//     whatever `consoleClientId` deploy-infra.yml passes in. Before a human
//     completes Phase 2, that parameter resolves (via
//     `${{ secrets.CONSOLE_ENTRA_CLIENT_ID || '00000000-...-000000000000' }}`
//     in deploy-infra.yml) to an invalid placeholder GUID — logins fail
//     closed against it (STRICTLY MORE restrictive than no-auth, so
//     AUTH-002 holds trivially during this window).
//   Phase 2 — a human with directory admin rights runs
//     scripts/bootstrap-console-auth.sh, which reads consoleIdentity's
//     real principalId/clientId (read-only `az identity show`) and prints
//     the exact manual Entra Portal steps: create the App Registration,
//     add a redirect URI, add a Federated Identity Credential whose
//     subject is consoleIdentity's principalId, then
//     `gh secret set CONSOLE_ENTRA_CLIENT_ID --env cmos-dev`.
//   Phase 3 — the NEXT deploy-infra.yml run picks up the real client ID as
//     an ordinary idempotent ARM incremental update — no identity
//     recreation, no downtime beyond a normal revision update.

@description('Azure region.')
param location string = resourceGroup().location

@description('Container App name.')
param appName string = 'ca-console'

@description('Resource id of the Container Apps managed environment (cae-cmos-dev).')
param environmentId string

@description('ACR login server, computed by the registry-consumption module and passed in as a plain string.')
param registryLoginServer string

@description('Container image repository name in the shared ACR.')
param imageName string = 'console'

@description('Container image tag deployed by this template. deploy-console.yml replaces it per-commit via az containerapp update.')
param containerImageTag string = 'latest'

@description('Required Entra App Registration client id for Easy Auth. No default (AUTH-003) — see the three-phase bootstrap note above.')
param consoleClientId string

@description('Entra tenant id used to build the OIDC issuer URL.')
param tenantId string = subscription().tenantId

@description('Application Insights connection string (console-app-insights module output).')
param applicationInsightsConnectionString string

@description('vault-api base URL used only when VAULT_API_MODE=real (INTEG-001). Defaults to a mock-mode placeholder value that is never dereferenced while VAULT_API_MODE=mock.')
param vaultApiBaseUrl string = 'https://vault.internal.cmos.dev'

@description('Gatekeeper base URL used only when GATEKEEPER_API_MODE=real (INTEG-002). Defaults to a mock-mode placeholder value that is never dereferenced while GATEKEEPER_API_MODE=mock.')
param gatekeeperApiBaseUrl string = 'https://gatekeeper.internal.cmos.dev'

@description('Resource id of the console user-assigned managed identity (console-identity.bicep module output) — created there, never looked up here.')
param consoleIdentityId string

@description('Client id (GUID) of the console user-assigned managed identity — becomes the value of the override-use-mi-fic-assertion-client-id secret, never a real credential.')
param consoleIdentityClientId string

var image = '${registryLoginServer}/${imageName}:${containerImageTag}'

resource consoleApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: appName
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${consoleIdentityId}': {}
    }
  }
  tags: {
    purpose: 'operator console — the only externally-reachable read surface in the platform'
  }
  properties: {
    environmentId: environmentId
    configuration: {
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false
      }
      secrets: [
        {
          // Sentinel secret NAME recognized by Container Apps Easy Auth —
          // its VALUE is the identity's own public client id (a GUID),
          // never a real credential. See the schema-verification comment
          // above. AUTH-001's verify permits exactly this one sentinel.
          name: 'override-use-mi-fic-assertion-client-id'
          value: consoleIdentityClientId
        }
        {
          name: 'appinsights-connection-string'
          value: applicationInsightsConnectionString
        }
      ]
      registries: [
        {
          server: registryLoginServer
          identity: consoleIdentityId
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'console'
          image: image
          env: [
            {
              name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
              secretRef: 'appinsights-connection-string'
            }
            {
              name: 'VAULT_API_MODE'
              value: 'mock'
            }
            {
              name: 'VAULT_API_BASE_URL'
              value: vaultApiBaseUrl
            }
            {
              name: 'GATEKEEPER_API_MODE'
              value: 'mock'
            }
            {
              name: 'GATEKEEPER_API_BASE_URL'
              value: gatekeeperApiBaseUrl
            }
            {
              name: 'TENANT_ID'
              value: tenantId
            }
          ]
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: '/health'
                port: 8000
              }
              initialDelaySeconds: 5
              periodSeconds: 15
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

// Entra ID Easy Auth — secretless via Federated Identity Credential
// (L-0013). unauthenticatedClientAction='Return401' rejects every
// unauthenticated request outright (AUTH-002), never falling through to
// an implicit-grant redirect that would leak app data.
resource consoleAuth 'Microsoft.App/containerApps/authConfigs@2024-10-02-preview' = {
  parent: consoleApp
  name: 'current'
  properties: {
    platform: {
      enabled: true
    }
    globalValidation: {
      unauthenticatedClientAction: 'Return401'
    }
    identityProviders: {
      azureActiveDirectory: {
        enabled: true
        registration: {
          clientId: consoleClientId
          clientSecretSettingName: 'override-use-mi-fic-assertion-client-id'
          openIdIssuer: 'https://login.microsoftonline.com/${tenantId}/v2.0'
        }
        validation: {
          defaultAuthorizationPolicy: {
            allowedApplications: []
          }
        }
      }
    }
  }
}

output appName string = consoleApp.name
output appId string = consoleApp.id
output fqdn string = consoleApp.properties.configuration.ingress.fqdn
