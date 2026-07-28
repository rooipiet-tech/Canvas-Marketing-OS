// Canvas Marketing OS — infra/modules/vault/smoke-test-job.bicep
//
// caj-vault-smoke-test — runs the COMPLETE
// services/vault/tests/test_contract_smoke.py suite (every parametrized
// taxonomy/consent/dedup/retention/rollup/bulk-timing case) from inside
// cae-cmos-dev's VNet against ca-vault's internal-ingress-only endpoint
// (AC-002 through AC-009, AC-013, AC-015). Same Container Apps Job
// mechanism and ACR-pull-via-managed-identity pattern as
// retention-expiry-job.bicep; reuses the same vault image (its
// Dockerfile also COPYs requirements-test.txt and tests/, so no second
// image is built for this job).

@description('Azure region.')
param location string = resourceGroup().location

@description('Container Apps Job name.')
param jobName string = 'caj-vault-smoke-test'

@description('Resource id of the Container Apps managed environment (cae-cmos-dev).')
param environmentId string

@description('Postgres server fully-qualified domain name.')
param postgresFqdn string

@description('Postgres administrator login.')
param administratorLogin string

@secure()
@description('Postgres administrator login password.')
param administratorLoginPassword string

@description('Postgres database name the Vault service connects to.')
param databaseName string = 'postgres'

@description('Shared ACR login server (infra/modules/container-registry.bicep output).')
param acrLoginServer string

@description('Shared ACR resource name, for the AcrPull role assignment scope.')
param acrRegistryName string

@description('Resource id of the shared ACR (informational — role assignment scope is resolved from acrRegistryName).')
param acrRegistryId string

@description('Vault service image reference, e.g. <acrLoginServer>/vault:<tag>.')
param vaultImage string

@description('Internal ingress base URL of ca-vault, e.g. https://ca-vault.internal.<env-domain>.')
param vaultBaseUrl string

resource acrRegistry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: acrRegistryName
}

var databaseUrl = 'postgresql://${administratorLogin}:${administratorLoginPassword}@${postgresFqdn}:5432/${databaseName}?sslmode=require'

resource smokeTestJob 'Microsoft.App/jobs@2024-03-01' = {
  name: jobName
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  tags: {
    purpose: 'full contract-smoke suite against the deployed ca-vault dev endpoint'
  }
  properties: {
    environmentId: environmentId
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: 900
      replicaRetryLimit: 0
      manualTriggerConfig: {
        replicaCompletionCount: 1
        parallelism: 1
      }
      secrets: [
        {
          name: 'db-connection-string'
          value: databaseUrl
        }
      ]
      registries: [
        {
          server: acrLoginServer
          identity: 'system'
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'vault-smoke-test'
          image: vaultImage
          command: [
            'sh'
            '-c'
            'pip install --no-cache-dir -r requirements-test.txt && pytest tests/ -v'
          ]
          env: [
            {
              name: 'DATABASE_URL'
              secretRef: 'db-connection-string'
            }
            {
              name: 'VAULT_BASE_URL'
              value: vaultBaseUrl
            }
          ]
          resources: {
            cpu: json('1.0')
            memory: '2Gi'
          }
        }
      ]
    }
  }
}

resource smokeTestJobAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acrRegistryId, smokeTestJob.name, 'AcrPull')
  scope: acrRegistry
  properties: {
    principalId: smokeTestJob.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '7f951dda-4ed3-4680-a7ca-43fe172d538d'
    )
  }
}

output jobName string = smokeTestJob.name
output jobId string = smokeTestJob.id
