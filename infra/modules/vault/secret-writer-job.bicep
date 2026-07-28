// Canvas Marketing OS — infra/modules/vault/secret-writer-job.bicep
//
// caj-vault-secret-writer — the in-VNet secret-loading Container Apps Job
// L-0012 recommends: it loads the Vault DB connection string into Key
// Vault (secret name `vault-db-connection-string`) from *inside* the
// VNet, using its own system-assigned managed identity, so
// publicNetworkAccess on kv-cmos-dev-* is NEVER flipped to Enabled for
// this — not even temporarily. Mirrors migration-job.bicep/
// vault-query-job.bicep's one-shot Container Apps Job mechanism, just
// with an azure-cli image instead of postgres so it can call the Key
// Vault data plane instead of Postgres.
//
// Role assignment scope (plan F7): granting "Key Vault Secrets Officer"
// scoped to the specific vault-db-connection-string SECRET resource id
// isn't expressible in Bicep before the secret exists (the job, not
// Bicep, creates it at runtime — the plaintext password never enters the
// ARM template/deployment history). The vault-wide grant below is a
// documented first-run bootstrap exception (see docs/credentials-runbook.md);
// narrowing it to the specific secret's resource id once it exists is
// tracked as vault-hardening follow-up, not done by this module.

@description('Azure region.')
param location string = resourceGroup().location

@description('Container Apps Job name.')
param jobName string = 'caj-vault-secret-writer'

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

@description('Key Vault name to write the secret into.')
param keyVaultName string

@description('Key Vault secret name for the Vault DB connection string.')
param secretName string = 'vault-db-connection-string'

@description('Resource id of the Key Vault, for the bootstrap role assignment scope (informational — the role assignment below scopes to the Key Vault resource itself, resolved from keyVaultName).')
param keyVaultId string

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

var databaseUrl = 'postgresql://${administratorLogin}:${administratorLoginPassword}@${postgresFqdn}:5432/${databaseName}?sslmode=require'

resource secretWriterJob 'Microsoft.App/jobs@2024-03-01' = {
  name: jobName
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  tags: {
    purpose: 'in-VNet Key Vault secret loader (never opens publicNetworkAccess — L-0012)'
  }
  properties: {
    environmentId: environmentId
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: 300
      replicaRetryLimit: 1
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
    }
    template: {
      containers: [
        {
          name: 'vault-secret-writer'
          image: 'mcr.microsoft.com/azure-cli:latest'
          command: [
            'sh'
            '-c'
            'az login --identity --allow-no-subscriptions >/dev/null && az keyvault secret set --vault-name "$KEY_VAULT_NAME" --name "$SECRET_NAME" --value "$DATABASE_URL" --output none'
          ]
          env: [
            {
              name: 'DATABASE_URL'
              secretRef: 'db-connection-string'
            }
            {
              name: 'KEY_VAULT_NAME'
              value: keyVaultName
            }
            {
              name: 'SECRET_NAME'
              value: secretName
            }
          ]
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
        }
      ]
    }
  }
}

// First-run bootstrap exception (see header comment): vault-wide Key
// Vault Secrets Officer, narrowed to the specific secret in a future
// hardening pass once the secret resource exists.
resource secretWriterKvRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVaultId, secretWriterJob.name, 'Key Vault Secrets Officer')
  scope: keyVault
  properties: {
    principalId: secretWriterJob.identity.principalId
    principalType: 'ServicePrincipal'
    // Key Vault Secrets Officer
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      'b86a8fe4-44ce-4948-aee5-eccb2c155cd7'
    )
  }
}

output jobName string = secretWriterJob.name
output jobId string = secretWriterJob.id
output principalId string = secretWriterJob.identity.principalId
