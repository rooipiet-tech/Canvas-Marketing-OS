// Canvas Marketing OS — gatekeeper-approval-app.bicep
//
// ca-gatekeeper-approval: the ONLY externally reachable governance
// surface. It serves exactly one functional route,
// GET /approval-action/{link_token}, and nothing else — the internal
// APIs live on ca-gatekeeper with external: false.
//
// AUTHENTICATION IS PLATFORM-ENFORCED
// -----------------------------------
// The authConfigs child resource below turns on Container Apps built-in
// authentication with an Entra ID (Azure AD) identity provider and
// unauthenticatedClientAction: 'Return401'. An anonymous request never
// reaches the container: the platform terminates the login and injects
// X-MS-CLIENT-PRINCIPAL-* headers, which services/gatekeeper/app/auth.py
// turns into the recorded approver. Possession of an approval link
// therefore proves nothing about identity.
//
// FOLLOW-UP REQUIRED BEFORE THIS IS USABLE BY A HUMAN
// --------------------------------------------------
// `aadClientId` defaults to the all-zero placeholder GUID because no
// Entra ID app registration exists for this app yet. Creating that
// registration (with the app's /.auth/login/aad/callback redirect URI)
// and supplying its client id is a manual, human step — it cannot be
// done from a template. Until then the Bicep is valid and deploys, but
// the login flow will not complete. This is deliberately out of scope
// per the spec: AC-35's smoke test exercises the gate/sign/verify/replay
// cycle, not the interactive Entra sign-in.
//
// This module shares gatekeeper-bundle-unpack.sh with gatekeeper-app.bicep
// verbatim; the ONLY difference is APP_MODULE.

@description('Azure region.')
param location string = resourceGroup().location

@description('Container App name.')
param appName string = 'ca-gatekeeper-approval'

@description('Resource id of the Container Apps managed environment (cae-cmos-dev).')
param environmentId string

@secure()
@description('The SAME bundle JSON gatekeeper-app.bicep receives, built by main.bicep.')
param bundleJson string

@description('Verbatim contents of infra/modules/governance/gatekeeper-bundle-unpack.sh.')
param unpackScript string

@description('ASGI target — the approval-only app, not the internal API.')
param appModule string = 'approval_main:app'

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

@description('Tenant id backing the Entra ID identity provider.')
param tenantId string = subscription().tenantId

@description('PLACEHOLDER — client id of the Entra ID app registration protecting this app. Must be replaced by a real registration before a human can sign in (see the module header).')
param aadClientId string = '00000000-0000-0000-0000-000000000000'

var bundleBase64 = base64(bundleJson)

resource approvalApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: appName
  location: location
  tags: {
    purpose: 'entra-id-protected approval action surface'
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
        external: true
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
      secrets: [
        {
          name: 'bundle-b64'
          value: bundleBase64
        }
        {
          name: 'db-connection-string'
          value: databaseUrl
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'gatekeeper-approval'
          image: 'python:3.12-slim'
          command: [
            'sh'
            '-c'
            unpackScript
          ]
          env: [
            {
              name: 'BUNDLE_B64'
              secretRef: 'bundle-b64'
            }
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
              name: 'AZURE_CLIENT_ID'
              value: userAssignedClientId
            }
          ]
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
        }
      ]
      // Kept warm: an approver clicking a link should not wait on a cold
      // start, and a timed-out click looks identical to a broken link.
      // This is the ONLY governance app that pins a warm replica floor.
      scale: {
        minReplicas: 1
        maxReplicas: 3
      }
    }
  }
}

resource approvalAuthConfig 'Microsoft.App/containerApps/authConfigs@2024-03-01' = {
  parent: approvalApp
  name: 'current'
  properties: {
    platform: {
      enabled: true
    }
    globalValidation: {
      unauthenticatedClientAction: 'Return401'
      redirectToProvider: 'azureactivedirectory'
    }
    identityProviders: {
      azureActiveDirectory: {
        enabled: true
        registration: {
          openIdIssuer: '${environment().authentication.loginEndpoint}${tenantId}/v2.0'
          clientId: aadClientId
        }
        validation: {
          allowedAudiences: [
            aadClientId
          ]
        }
      }
    }
    login: {
      preserveUrlFragmentsForLogins: true
    }
  }
}

output appName string = approvalApp.name
output appId string = approvalApp.id
output externalFqdn string = approvalApp.properties.configuration.ingress.fqdn
output approvalBaseUrl string = 'https://${approvalApp.properties.configuration.ingress.fqdn}'
output authConfigName string = approvalAuthConfig.name
