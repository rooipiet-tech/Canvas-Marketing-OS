// Canvas Marketing OS — publisher-app.bicep
//
// ca-publisher: the INTERNAL Publisher API (POST /publish,
// GET /publish-attempts/{id}). external: false — Publisher has no
// externally reachable surface at all. It is the only service ever
// permitted to hold external write credentials, so keeping it entirely
// off the public internet matters more here than anywhere else.
//
// Identity: id-cmos-publisher holds a CUSTOM verify-only Key Vault role
// (read the key, verify a signature). It cannot sign, and it cannot read
// secrets — see signing-key.bicep.
//
// Source arrives as a base64'd JSON bundle secret (Container Apps
// collapses a literal "$$" in secret values to "$", L-0012) and is
// unpacked by publisher-bundle-unpack.sh, consumed VERBATIM here as
// `unpackScript`. No pip/launch logic is written in this file.

@description('Azure region.')
param location string = resourceGroup().location

@description('Container App name.')
param appName string = 'ca-publisher'

@description('Resource id of the Container Apps managed environment (cae-cmos-dev).')
param environmentId string

@secure()
@description('Part 0 of 4: JSON object {relative path: file content} built by main.bicep from services/publisher/BUNDLE_MANIFEST.txt. Split across four params, never one, so no single base64-encoded value approaches ARM template-expression literal limit -- see main.bicep, comment above gatekeeperBundlePart0.')
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

@description('Verbatim contents of infra/modules/governance/publisher-bundle-unpack.sh.')
param unpackScript string

@description('ASGI target inside the bundle.')
param appModule string = 'main:app'

@description('Resource id of the user-assigned managed identity Publisher runs as.')
param userAssignedIdentityId string

@description('Client id of that identity, so DefaultAzureCredential picks the right one.')
param userAssignedClientId string

@secure()
@description('Postgres connection string for the governance + Vault schemas.')
param databaseUrl string

@description('Key Vault URI holding the gate-token signing key (public half only, for verification).')
param keyVaultUri string

@description('Name of the gate-token signing key inside the vault.')
param signingKeyName string

@description('Gate-token iss claim Publisher requires.')
param tokenIssuer string = 'cmos-gatekeeper'

@description('Gate-token aud claim Publisher requires.')
param tokenAudience string = 'cmos-publisher'

@description('Comma-separated pinned algorithm allowlist. RS256 only (this Key Vault SKU has no EdDSA key type).')
param tokenAlgorithms string = 'RS256'

@description('Changes on every deploy (main.bicep defaults it to utcNow()) so this app always gets a NEW revision — see gatekeeper-app.bicep for why this is required (a secret-value-only change, like a rotated Postgres password, does not otherwise force a new revision, leaving the running replica stuck with the stale value it booted with).')
param deployToken string

// Four INDEPENDENT secrets, one per bundle part, never one combined
// blob. Each part is base64-encoded SEPARATELY here -- this is the fix
// itself, not just packaging: computing base64() of the four already-
// small parts individually is what keeps every one of these variables
// under ARM's 131072-character template-expression literal limit, a
// limit that binds regardless of any downstream chunking (see
// main.bicep's own comment above gatekeeperBundlePart0 for the full
// story of the deploy failure this replaced, first hit on Gatekeeper's
// own bundle -- Publisher was not yet over it but is fixed the same way
// for consistency and headroom). Only parts the bundle actually needs
// become secrets (filter drops the empty ones) -- an empty-string
// Container Apps secret value is untested territory this avoids
// entirely rather than relying on it being accepted.
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

resource publisherApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: appName
  location: location
  tags: {
    purpose: 'publisher internal API'
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
      ])
    }
    template: {
      revisionSuffix: 'r${uniqueString(deployToken)}'
      containers: [
        {
          name: 'publisher'
          image: 'python:3.12-slim'
          command: [
            'sh'
            '-c'
            unpackScript
          ]
          env: concat(bundleChunkEnvEntries, [
            {
              name: 'DATABASE_URL'
              secretRef: 'db-connection-string'
            }
            {
              name: 'APP_MODULE'
              value: appModule
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
              name: 'GATE_TOKEN_ALGORITHMS'
              value: tokenAlgorithms
            }
            {
              name: 'AZURE_CLIENT_ID'
              value: userAssignedClientId
            }
          ])
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
        }
      ]
      // minReplicas: 1 — see gatekeeper-app.bicep's identical comment.
      // Confirmed live: this is the exact app that hit the cold-start
      // timeout in the round-2 deploy — its first-ever request (the smoke
      // test's /publish call) arrived before the container had even started
      // installing dependencies, and Uvicorn wasn't ready until ~7s after
      // the caller's 30s read timeout had already fired.
      scale: {
        minReplicas: 1
        maxReplicas: 3
      }
    }
  }
}

output appName string = publisherApp.name
output appId string = publisherApp.id
output internalFqdn string = publisherApp.properties.configuration.ingress.fqdn
