// Canvas Marketing OS — infra/modules/vault/smoke-test-job.bicep
//
// caj-vault-smoke-test — runs the COMPLETE
// services/vault/tests/test_contract_smoke.py suite (every parametrized
// taxonomy/consent/dedup/retention/rollup/bulk-timing case) from inside
// cae-cmos-dev's VNet against ca-vault's internal-ingress-only endpoint
// (AC-002 through AC-009, AC-013, AC-015). Same Container Apps Job
// mechanism as retention-expiry-job.bicep; reuses the same vault image
// (its Dockerfile also COPYs requirements-test.txt and tests/, so no
// second image is built for this job).
//
// PATCH: pulls its image via the pre-provisioned USER-ASSIGNED managed
// identity (managed-identity.bicep's id-vault) rather than a
// system-assigned identity — see that file's header for the confirmed
// live chicken-and-egg ordering bug this avoids. This job needs no
// other Azure data-plane access (only Postgres, via connection string,
// and an HTTP call to ca-vault's internal FQDN), so it carries no
// system-assigned identity at all — user-assigned only.

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

@description('Resource id of the shared ACR (informational — see managed-identity.bicep for the AcrPull grant).')
param acrRegistryId string

@description('Resource id of the pre-provisioned user-assigned managed identity (managed-identity.bicep\'s id-vault), used to pull vaultImage from acrLoginServer without the system-assigned-identity ordering bug.')
param userAssignedIdentityId string

@description('Vault service image reference, e.g. <acrLoginServer>/vault:<tag>.')
param vaultImage string

@description('Internal ingress base URL of ca-vault, e.g. https://ca-vault.internal.<env-domain>.')
param vaultBaseUrl string

var databaseUrl = 'postgresql://${administratorLogin}:${administratorLoginPassword}@${postgresFqdn}:5432/${databaseName}?sslmode=require'

resource smokeTestJob 'Microsoft.App/jobs@2024-03-01' = {
  name: jobName
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${userAssignedIdentityId}': {}
    }
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
          identity: userAssignedIdentityId
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

// No AcrPull role assignment here — smokeTestJob pulls its image via
// userAssignedIdentityId, which managed-identity.bicep already grants
// AcrPull to, independently and ahead of this resource.

output jobName string = smokeTestJob.name
output jobId string = smokeTestJob.id
