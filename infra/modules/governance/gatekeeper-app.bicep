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
@description('Part 0 of 4: JSON object {relative path: file content} built by main.bicep from services/gatekeeper/BUNDLE_MANIFEST.txt. Split across four params, never one, so no single base64-encoded value approaches ARM template-expression literal limit -- see main.bicep, comment above gatekeeperBundlePart0.')
param bundleJsonPart0 string

@secure()
@description('Part 1 of 4 -- see bundleJsonPart0.')
param bundleJsonPart1 string

@secure()
@description('Part 2 of 4 -- see bundleJsonPart0.')
param bundleJsonPart2 string

@secure()
@description('Part 3 of 4 -- see bundleJsonPart0.')
param bundleJsonPart3 string

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

// Four INDEPENDENT secrets, one per bundle part, never one combined
// blob. Each part is base64-encoded SEPARATELY here -- this is the fix
// itself, not just packaging: computing base64() of the four already-
// small parts individually is what keeps every one of these variables
// under ARM's 131072-character template-expression literal limit, a
// limit that binds regardless of any downstream chunking (see
// main.bicep's own comment above gatekeeperBundlePart0 for the full
// story of the deploy failure this replaced). Only parts the bundle
// actually needs become secrets (filter drops the empty ones) -- an
// empty-string Container Apps secret value is untested territory this
// avoids entirely rather than relying on it being accepted.
var bundleChunkCandidates = [
  {
    name: 'bundle-b64-part0'
    value: empty(bundleJsonPart0) ? '' : base64(bundleJsonPart0)
  }
  {
    name: 'bundle-b64-part1'
    value: empty(bundleJsonPart1) ? '' : base64(bundleJsonPart1)
  }
  {
    name: 'bundle-b64-part2'
    value: empty(bundleJsonPart2) ? '' : base64(bundleJsonPart2)
  }
  {
    name: 'bundle-b64-part3'
    value: empty(bundleJsonPart3) ? '' : base64(bundleJsonPart3)
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
