// Canvas Marketing OS — main.bicep
//
// Resource-group-scoped orchestrator. Deploy into an already-created
// cmos-dev resource group (see .github/workflows/deploy-infra.yml's
// preflight job — `az group create` runs before this template).
//
// Dependency graph:
//   network -> private-dns
//     -> { container-apps-environment, postgres, service-bus, key-vault,
//          storage }   (parallel; none of these four depend on each
//                       other or on postgres — see plan-full.json F6)
//     -> migration-job   (dependsOn: postgres, container-apps-environment)
//     -> vault-query-job (dependsOn: postgres, container-apps-environment)

targetScope = 'resourceGroup'

@description('Azure region for all resources.')
param location string = 'southafricanorth'

@secure()
@description('Postgres administrator login password. No default — supplied at deploy time by the workflow (openssl rand).')
param administratorLoginPassword string

@description('Non-secret Postgres administrator login name.')
param administratorLogin string = 'cmosadmin'

// Loaded once here (single ../, since main.bicep sits at infra/main.bicep,
// one level below repo root, same level as /contracts) and threaded down
// as a plain parameter — child modules never call loadTextContent
// themselves.
var vaultSchemaSql = loadTextContent('../contracts/vault-schema/schema.sql')

module network 'modules/network.bicep' = {
  name: 'network'
  params: {
    location: location
  }
}

module privateDns 'modules/private-dns.bicep' = {
  name: 'private-dns'
  params: {
    vnetId: network.outputs.vnetId
  }
}

module containerAppsEnvironment 'modules/container-apps-environment.bicep' = {
  name: 'container-apps-environment'
  params: {
    location: location
    infrastructureSubnetId: network.outputs.caeInfraSubnetId
  }
  dependsOn: [
    privateDns
  ]
}

module postgres 'modules/postgres.bicep' = {
  name: 'postgres'
  params: {
    location: location
    administratorLogin: administratorLogin
    administratorLoginPassword: administratorLoginPassword
    privateEndpointSubnetId: network.outputs.peSubnetId
    postgresPrivateDnsZoneId: privateDns.outputs.postgresZoneId
  }
  dependsOn: [
    privateDns
  ]
}

module serviceBus 'modules/service-bus.bicep' = {
  name: 'service-bus'
  params: {
    location: location
  }
  dependsOn: [
    privateDns
  ]
}

module keyVault 'modules/key-vault.bicep' = {
  name: 'key-vault'
  params: {
    location: location
    privateEndpointSubnetId: network.outputs.peSubnetId
    vaultPrivateDnsZoneId: privateDns.outputs.vaultZoneId
  }
  dependsOn: [
    privateDns
  ]
}

module storage 'modules/storage.bicep' = {
  name: 'storage'
  params: {
    location: location
    privateEndpointSubnetId: network.outputs.peSubnetId
    blobPrivateDnsZoneId: privateDns.outputs.blobZoneId
  }
  dependsOn: [
    privateDns
  ]
}

module migrationJob 'modules/migration-job.bicep' = {
  name: 'migration-job'
  params: {
    location: location
    environmentId: containerAppsEnvironment.outputs.environmentId
    postgresFqdn: postgres.outputs.fqdn
    administratorLogin: administratorLogin
    administratorLoginPassword: administratorLoginPassword
    schemaSql: vaultSchemaSql
  }
  dependsOn: [
    postgres
    containerAppsEnvironment
  ]
}

module vaultQueryJob 'modules/vault-query-job.bicep' = {
  name: 'vault-query-job'
  params: {
    location: location
    environmentId: containerAppsEnvironment.outputs.environmentId
    postgresFqdn: postgres.outputs.fqdn
    administratorLogin: administratorLogin
    administratorLoginPassword: administratorLoginPassword
  }
  dependsOn: [
    postgres
    containerAppsEnvironment
  ]
}

// --- S1 model-gateway wiring (session s1-gateway) — insertion point ---
//
// The registry name is computed HERE, once, and passed as a plain string to
// both modules. That is deliberate and load-bearing: gateway.bicep needs the
// ACR login server, and container-registry.bicep needs the gateway's
// managed-identity principalId. If gateway.bicep read
// containerRegistry.outputs.loginServer instead, the two modules would form
// a genuine circular dependency and Bicep would refuse to compile.
var containerRegistryName = 'acrcmosshared${uniqueString(resourceGroup().id)}'
var containerRegistryLoginServer = '${containerRegistryName}.azurecr.io'

module gateway 'modules/gateway.bicep' = {
  name: 'gateway'
  params: {
    location: location
    environmentId: containerAppsEnvironment.outputs.environmentId
    keyVaultName: keyVault.outputs.vaultName
    postgresFqdn: postgres.outputs.fqdn
    administratorLogin: administratorLogin
    administratorLoginPassword: administratorLoginPassword
    containerRegistryLoginServer: containerRegistryLoginServer
  }
  dependsOn: [
    postgres
    containerAppsEnvironment
    keyVault
  ]
}

module containerRegistry 'modules/container-registry.bicep' = {
  name: 'container-registry'
  params: {
    location: location
    registryName: containerRegistryName
    // Referencing the gateway module's output is what orders these two.
    pullPrincipalId: gateway.outputs.principalId
  }
}

output vnetId string = network.outputs.vnetId
output containerAppsEnvironmentName string = containerAppsEnvironment.outputs.environmentName
output postgresServerName string = postgres.outputs.serverName
output serviceBusNamespaceName string = serviceBus.outputs.namespaceName
output keyVaultName string = keyVault.outputs.vaultName
output storageAccountName string = storage.outputs.storageAccountName
output migrationJobName string = migrationJob.outputs.jobName
output vaultQueryJobName string = vaultQueryJob.outputs.jobName
output gatewayAppName string = gateway.outputs.appName
output gatewayPrincipalId string = gateway.outputs.principalId
output containerRegistryLoginServer string = containerRegistry.outputs.loginServer
output containerRegistryName string = containerRegistry.outputs.registryName
