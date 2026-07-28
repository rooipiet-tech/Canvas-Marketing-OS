// Canvas Marketing OS — gateway.bicep
//
// The model-gateway Container App: a SystemAssigned managed identity,
// internal ingress only (matching the frozen contract's canonical server
// url https://model-gateway.internal.cmos.dev), pulling its image from the
// shared ACR with that same identity — no registry admin credential exists.
//
// KEY VAULT ACCESS (L-0011): a Key Vault deployed with
// enableRbacAuthorization: true grants NO data-plane access to anyone by
// default — not even a subscription Owner. The Container App's native Key
// Vault secret reference below therefore only works because of the explicit
// "Key Vault Secrets User" role assignment at the bottom of this file. This
// is the first Microsoft.Authorization/roleAssignments resource anywhere in
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
// EXPECTED TRANSIENT STATE ON FIRST DEPLOY (not a bug): infra deployment
// creates this Container App before deploy-gateway.yml's build-and-push job
// has ever pushed an image, so on a brand-new environment the ACR is empty
// and this app's first revision cannot pull ${imageName}:${imageTag}. The
// revision shows as unhealthy/failed-to-pull until the first successful
// docker push + `az containerapp update` from deploy-gateway.yml completes,
// at which point it self-resolves. Also noted in docs/accepted-risks.md.

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

@description('ACR login server, computed once in main.bicep and passed in as a plain string so this module never depends on the container-registry module (which itself depends on this app\'s principalId).')
param containerRegistryLoginServer string

@description('Container image repository name in the shared ACR.')
param imageName string = 'model-gateway'

@description('Container image tag deployed by this template. deploy-gateway.yml replaces it per-commit via az containerapp update.')
param imageTag string = 'latest'

@description('Name of the Key Vault secret holding the upstream provider API key.')
param anthropicSecretName string = 'anthropic-api-key'

// Same connection-string convention as migration-job.bicep / vault-query-job.bicep.
var databaseUrl = 'postgresql://${administratorLogin}:${administratorLoginPassword}@${postgresFqdn}:5432/${databaseName}?sslmode=require'
var image = '${containerRegistryLoginServer}/${imageName}:${imageTag}'
var vaultUri = 'https://${keyVaultName}.vault.azure.net/'

resource kv 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

resource gatewayApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: appName
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  tags: {
    purpose: 'model gateway (internal ingress only)'
  }
  properties: {
    environmentId: environmentId
    configuration: {
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
          // this app's system-assigned identity — see the role assignment below.
          name: 'anthropic-api-key'
          keyVaultUrl: '${vaultUri}secrets/${anthropicSecretName}'
          identity: 'system'
        }
      ]
      registries: [
        {
          server: containerRegistryLoginServer
          identity: 'system'
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'model-gateway'
          image: image
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

// Key Vault Secrets User (4633458b-17de-408a-b874-0445c86b69e6) for the
// gateway's managed identity, scoped to the vault. Without this, the secret
// reference above resolves to a Forbidden error at revision start.
resource kvSecretsUserRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(kv.id, gatewayApp.id, 'kv-secrets-user')
  scope: kv
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
    principalId: gatewayApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

output appName string = gatewayApp.name
output appId string = gatewayApp.id
output principalId string = gatewayApp.identity.principalId
output fqdn string = gatewayApp.properties.configuration.ingress.fqdn
