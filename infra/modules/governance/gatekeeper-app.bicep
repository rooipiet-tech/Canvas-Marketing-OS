// Canvas Marketing OS — gatekeeper-app.bicep
//
// ca-gatekeeper: the INTERNAL Gatekeeper API (POST /gate-check,
// GET /decisions/{id}). external: false — reachable only from inside the
// VNet. The approve/reject click surface is a physically separate app
// (gatekeeper-approval-app.bicep), which is the only externally
// reachable governance route in this session.
//
// No Dockerfile / no ACR: this mirrors the repo's established
// stock-image + inline-command pattern (migration-job.bicep). The
// service source arrives as a base64'd JSON bundle secret and is
// unpacked by gatekeeper-bundle-unpack.sh, which this module consumes
// VERBATIM as `unpackScript` — there is deliberately no pip/launch logic
// written in this file, so the bicep path and
// scripts/verify_governance_bundle_reconstruction.py can never diverge.
//
// The bundle is base64-encoded before becoming a Container Apps secret:
// Container Apps collapses a literal "$$" in a secret value to "$"
// (L-0012), and Python source is full of dollar signs in regexes and
// f-strings. The base64 alphabet contains no "$" at all.

@description('Azure region.')
param location string = resourceGroup().location

@description('Container App name.')
param appName string = 'ca-gatekeeper'

@description('Resource id of the Container Apps managed environment (cae-cmos-dev).')
param environmentId string

@secure()
@description('JSON object {relative path: file content} built by main.bicep from services/gatekeeper/BUNDLE_MANIFEST.txt.')
param bundleJson string

@description('Verbatim contents of infra/modules/governance/gatekeeper-bundle-unpack.sh.')
param unpackScript string

@description('ASGI target inside the bundle.')
param appModule string = 'main:app'

@description('Resource id of the user-assigned managed identity Gatekeeper runs as.')
param userAssignedIdentityId string

@description('Client id of that identity, so DefaultAzureCredential picks the right one.')
param userAssignedClientId string

@secure()
@description('Postgres connection string for the governance + Vault schemas.')
param databaseUrl string

@description('Key Vault URI holding the gate-token signing key.')
param keyVaultUri string

@description('Name of the gate-token signing key inside the vault.')
param signingKeyName string

@description('Public base URL of the Entra-ID-protected approval-action app.')
param approvalBaseUrl string

@description('Key Vault URL of the teams-webhook-url secret (Workflows/Power Automate HTTP-trigger URL for Adaptive Card approval notifications). id-cmos-gatekeeper already holds Key Vault Secrets User on this vault (see signing-key.bicep) so no RBAC change is needed here — only the secret reference + env wiring.')
param teamsWebhookUrlKeyVaultUrl string

@description('Gate-token iss claim.')
param tokenIssuer string = 'cmos-gatekeeper'

@description('Gate-token aud claim.')
param tokenAudience string = 'cmos-publisher'

@description('Changes on every deploy (main.bicep defaults it to utcNow()) so this app always gets a NEW revision. Confirmed live: with activeRevisionsMode Single and nothing else in the template changing, a redeploy that only changes a secret VALUE (e.g. databaseUrl, when the Postgres admin password rotates) does NOT create a new revision — the already-running replica keeps its original process environment (and therefore its original, now-stale DATABASE_URL) indefinitely. Forcing a fresh revisionSuffix every deploy is what actually restarts the container and picks up the current secret values.')
param deployToken string

// Chunked into up to 4 secrets/env vars, never one — see
// gatekeeper-bundle-unpack.sh's header on the 128 KiB MAX_ARG_STRLEN
// ceiling this bundle already crossed once at 27 files. Only chunks the
// bundle actually needs become secrets (filter drops the empty ones) --
// an empty-string Container Apps secret value is untested territory this
// avoids entirely rather than relying on it being accepted.
var bundleBase64 = base64(bundleJson)
var bundleChunkSize = 120000
var bundleLength = length(bundleBase64)
var bundleChunkCandidates = [
  {
    name: 'bundle-b64-0'
    value: bundleLength > 0 * bundleChunkSize
      ? substring(bundleBase64, 0 * bundleChunkSize, min(bundleChunkSize, max(0, bundleLength - 0 * bundleChunkSize)))
      : ''
  }
  {
    name: 'bundle-b64-1'
    value: bundleLength > 1 * bundleChunkSize
      ? substring(bundleBase64, 1 * bundleChunkSize, min(bundleChunkSize, max(0, bundleLength - 1 * bundleChunkSize)))
      : ''
  }
  {
    name: 'bundle-b64-2'
    value: bundleLength > 2 * bundleChunkSize
      ? substring(bundleBase64, 2 * bundleChunkSize, min(bundleChunkSize, max(0, bundleLength - 2 * bundleChunkSize)))
      : ''
  }
  {
    name: 'bundle-b64-3'
    value: bundleLength > 3 * bundleChunkSize
      ? substring(bundleBase64, 3 * bundleChunkSize, min(bundleChunkSize, max(0, bundleLength - 3 * bundleChunkSize)))
      : ''
  }
]
var bundleChunkSecrets = filter(bundleChunkCandidates, c => c.value != '')
var bundleChunkEnvEntries = [for c in bundleChunkSecrets: {
  name: toUpper(replace(c.name, '-', '_'))
  secretRef: c.name
}]

resource gatekeeperApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: appName
  location: location
  tags: {
    purpose: 'gatekeeper internal API'
  }
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
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false
        traffic: [
          {
            latestRevision: true
            weight: 100
          }
        ]
      }
      secrets: concat(bundleChunkSecrets, [
        {
          name: 'db-connection-string'
          value: databaseUrl
        }
        {
          name: 'teams-webhook-url'
          keyVaultUrl: teamsWebhookUrlKeyVaultUrl
          identity: userAssignedIdentityId
        }
      ])
    }
    template: {
      revisionSuffix: 'r${uniqueString(deployToken)}'
      containers: [
        {
          name: 'gatekeeper'
          image: 'python:3.12-slim'
          command: [
            'sh'
            '-c'
            unpackScript
          ]
          env: concat(
            bundleChunkEnvEntries,
            [
            {
              name: 'DATABASE_URL'
              secretRef: 'db-connection-string'
            }
            {
              name: 'APP_MODULE'
              value: appModule
            }
            {
              name: 'SIGNER_BACKEND'
              value: 'keyvault'
            }
            {
              name: 'KEY_VAULT_URL'
              value: keyVaultUri
            }
            {
              name: 'GATE_SIGNING_KEY_NAME'
              value: signingKeyName
            }
            {
              name: 'GATE_TOKEN_ISSUER'
              value: tokenIssuer
            }
            {
              name: 'GATE_TOKEN_AUDIENCE'
              value: tokenAudience
            }
            {
              name: 'APPROVAL_BASE_URL'
              value: approvalBaseUrl
            }
            {
              name: 'AZURE_CLIENT_ID'
              value: userAssignedClientId
            }
            {
              name: 'TEAMS_WEBHOOK_URL'
              secretRef: 'teams-webhook-url'
            }
          ])
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
        }
      ]
      // minReplicas: 1 — this is an always-on governance API every
      // gate-check call depends on, not a job. Without it, Container Apps'
      // default scale-to-zero means any idle period (including the gap
      // between a deploy finishing and the live smoke test's first
      // request) pays a cold-start tax — confirmed live: the round-2
      // deploy's smoke run sent its first /gate-check the moment the
      // deployment finished, and this app hadn't even started pip
      // installing yet, taking ~20s+ to reach Uvicorn-ready. gate-check's
      // own bounded retry (governance-smoke-test-job.bicep) absorbed that
      // once; a real caller shouldn't have to.
      scale: {
        minReplicas: 1
        maxReplicas: 3
      }
    }
  }
}

output appName string = gatekeeperApp.name
output appId string = gatekeeperApp.id
output internalFqdn string = gatekeeperApp.properties.configuration.ingress.fqdn
