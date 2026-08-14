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

// MAIDEN-DEPLOY INCIDENT (2026-07-31): ca-console's first-ever creation
// failed with ARM error IdentityDoesNotExist / "No managed service
// identities are associated with resource ca-console" — a confirmed,
// documented Azure Container Apps platform limitation (microsoft/
// azure-container-apps#1467): a user-assigned identity cannot be both
// newly attached via `identity.userAssignedIdentities` AND referenced by
// `registries[].identity` for ACR pull auth in the SAME initial create
// call — the identity must already be associated with the app from a
// PRIOR operation before it can be used for registry auth. (Every other
// container app in this repo that deployed cleanly was an idempotent
// re-apply of an already-existing app with an already-attached identity,
// not a genuine first-ever create combining both in one PUT — confirmed
// live via `az deployment operation group list`.)
//
// Fix: this template never declares `registries[]` at all — deploy-
// console.yml's CI pipeline owns the registry-pull identity exclusively,
// via `az containerapp registry set` (Microsoft's own documented reliable
// workaround for this exact bug), run once ca-console already exists.
// `containerImage` therefore defaults to a PUBLIC placeholder needing no
// registry auth at all, mirroring gateway.bicep's identical bootstrap
// pattern — deploy-infra.yml's preflight resolves it to the app's CURRENT
// live image once one exists, exactly like gatewayContainerImage.
@description('Console container image reference. deploy-infra.yml\'s preflight resolves this to the app\'s CURRENT live image if ca-console already exists, or a public placeholder on first-ever bootstrap — see the MAIDEN-DEPLOY INCIDENT comment above. Only deploy-console.yml (via `az containerapp update --image`, after `az containerapp registry set`) ever sets a real console image; this default is a documentation fallback for a direct `az deployment group create` run without that preflight step.')
param containerImage string = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

@description('Required Entra App Registration client id for Easy Auth. No default (AUTH-003) — see the three-phase bootstrap note above.')
param consoleClientId string

@description('Entra tenant id used to build the OIDC issuer URL.')
param tenantId string = subscription().tenantId

@description('Application Insights connection string (console-app-insights module output).')
param applicationInsightsConnectionString string

// L-0025: a hostname baked into a hardcoded default is a design INTENT, not
// proof the DNS/ingress actually resolves it — the fix is to resolve the
// consuming resource's own live binding, never guess an aspirational
// hostname. These two params therefore have NO default: main.bicep must
// pass ca-vault's and ca-gatekeeper's own `internalFqdn` outputs
// (Microsoft.App/containerApps ingress.fqdn — a real, live-verified value),
// so a future INTEG-001/INTEG-002 switch-to-real only has to flip
// VAULT_API_MODE/GATEKEEPER_API_MODE — the base URL is already correct on
// day one, not a placeholder someone has to remember to fix later.
@description('vault-api base URL used only when VAULT_API_MODE=real (INTEG-001). Caller must pass ca-vault\'s live internalFqdn-derived URL (see infra/main.bicep) — never dereferenced while VAULT_API_MODE=mock, but no hardcoded fallback is provided (L-0025).')
param vaultApiBaseUrl string

@description('Gatekeeper base URL used only when GATEKEEPER_API_MODE=real (INTEG-002). Caller must pass ca-gatekeeper\'s live internalFqdn-derived URL (see infra/main.bicep) — never dereferenced while GATEKEEPER_API_MODE=mock, but no hardcoded fallback is provided (L-0025).')
param gatekeeperApiBaseUrl string

@description('ca-orchestrator internal base URL, for GET /review/{task_id}\'s call to GET /tasks/{task_id}/review (F-TEAMS-CARD-REVIEW-LINK). Constructed in main.bicep from the shared Container Apps environment\'s defaultDomain, NOT from ca-orchestrator\'s own module output — avoids a circular module dependency (see main.bicep\'s comment). No mock mode for this client — an empty string here means the review page 503s with a clear message, same "config, not code" degrade every other unset base URL in this app gets.')
param orchestratorApiBaseUrl string

@description('Resource id of the console user-assigned managed identity (console-identity.bicep module output) — created there, never looked up here.')
param consoleIdentityId string

@description('Client id (GUID) of the console user-assigned managed identity — becomes the value of the override-use-mi-fic-assertion-client-id secret, never a real credential.')
param consoleIdentityClientId string

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
      // Deliberately NO registries[] block — see the MAIDEN-DEPLOY
      // INCIDENT comment on the containerImage param above. deploy-
      // console.yml's `az containerapp registry set` owns this exclusively
      // once ca-console exists; declaring it here would fight that CLI
      // step on every subsequent idempotent `az deployment group create`
      // re-apply (an omitted property is left untouched by an incremental
      // deployment; an explicit empty array here would wipe it instead).
    }
    template: {
      containers: [
        {
          name: 'console'
          image: containerImage
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
              name: 'ORCHESTRATOR_API_BASE_URL'
              value: orchestratorApiBaseUrl
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
// (L-0013).
//
// AUTH BUG FIX (live-reported 2026-07-31): a real browser GET to the
// console FQDN was returning a bare HTTP 401 instead of redirecting to
// Microsoft login — `unauthenticatedClientAction: 'Return401'` was the
// cause, confirmed live via `az containerapp auth show -n ca-console -g
// cmos-dev`. That value's original justification ("never falling through
// to an implicit-grant redirect that would leak app data") predates this
// session's live confirmation that the FIC is fully working end to end
// (App Registration `Canvas Marketing OS Console`, FIC
// `fic-console-managed-identity` on it, subject matching consoleIdentity's
// principalId, registration.clientId matching the real App Registration —
// all verified live via `az ad app show`/`az ad app federated-credential
// list`). With a working FIC, `RedirectToLoginPage` drives Easy Auth's
// standard SERVER-SIDE OAuth2 authorization-code flow — the FIC supplies
// the client assertion for the code-for-token exchange, so this is never
// the legacy client-side implicit-grant flow the original comment was
// guarding against. AUTH-002's frozen spec verify text already permitted
// this: "...both return HTTP 401, OR a 302 to login.microsoftonline.com
// whose body never contains app data markers" — a redirect response has
// no body content to leak, so this remains fully compliant while actually
// being usable by a human in a browser (Return401 alone gives a real
// operator no way to log in via the console's own UI at all).
//
// AGENT-002 compatibility: Easy Auth's `RedirectToLoginPage` still applies
// its own browser-vs-API heuristic (User-Agent/Accept-header based, same
// underlying EasyAuth engine App Service documents this behavior for) —
// it does not blanket-redirect every unauthenticated request. Plain,
// header-minimal callers (curl with no extra flags, this session's
// deploy-console.yml smoke checks, and programmatic agent clients) are
// classified as non-browser and still get a clean 401, preserving
// AGENT-002's documented "unauthenticated POST /kill-switch/toggle
// returns 401" contract — only requests that look like a real browser
// (Mozilla-style User-Agent, `Accept: text/html`) get redirected.
resource consoleAuth 'Microsoft.App/containerApps/authConfigs@2024-10-02-preview' = {
  parent: consoleApp
  name: 'current'
  properties: {
    platform: {
      enabled: true
    }
    globalValidation: {
      unauthenticatedClientAction: 'RedirectToLoginPage'
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
