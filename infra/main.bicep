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
//     -> migration-job    (postgres, container-apps-environment — via
//                           output references, no explicit dependsOn needed)
//     -> vault-query-job  (postgres, container-apps-environment — same)
//     -> container-registry  (no dependency on anything above)
//     -> gateway  (postgres, container-apps-environment, key-vault,
//                  container-registry — all via output references)
//
// DEPENDSON POLICY (fix/deploy-infra-gateway): a module's `dependsOn` block
// should list ONLY dependencies Bicep cannot already infer from a
// `moduleX.outputs.y` reference used in its own `params`. An explicit
// dependsOn entry for something already referenced that way is redundant —
// the linter flags it (no-unnecessary-dependson) — and, worse, an explicit
// dependsOn that ISN'T backed by a real output reference gives no actual
// ordering guarantee for the thing that matters (see gateway.bicep's header
// comment for the incident this caused). The remaining explicit dependsOn
// entries below (container-apps-environment, service-bus -> private-dns)
// are genuine: neither module reads any private-dns output, so nothing else
// would order them correctly.

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
    // postgresPrivateDnsZoneId already makes this depend on privateDns —
    // no explicit dependsOn needed (previously flagged by the linter).
    postgresPrivateDnsZoneId: privateDns.outputs.postgresZoneId
  }
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
    // vaultPrivateDnsZoneId already makes this depend on privateDns.
    vaultPrivateDnsZoneId: privateDns.outputs.vaultZoneId
  }
}

module storage 'modules/storage.bicep' = {
  name: 'storage'
  params: {
    location: location
    privateEndpointSubnetId: network.outputs.peSubnetId
    // blobPrivateDnsZoneId already makes this depend on privateDns.
    blobPrivateDnsZoneId: privateDns.outputs.blobZoneId
  }
}

module migrationJob 'modules/migration-job.bicep' = {
  name: 'migration-job'
  params: {
    location: location
    // environmentId and postgresFqdn already make this depend on
    // containerAppsEnvironment and postgres — no explicit dependsOn needed.
    environmentId: containerAppsEnvironment.outputs.environmentId
    postgresFqdn: postgres.outputs.fqdn
    administratorLogin: administratorLogin
    administratorLoginPassword: administratorLoginPassword
    schemaSql: vaultSchemaSql
  }
}

module vaultQueryJob 'modules/vault-query-job.bicep' = {
  name: 'vault-query-job'
  params: {
    location: location
    // Same as migration-job: environmentId/postgresFqdn already order this.
    environmentId: containerAppsEnvironment.outputs.environmentId
    postgresFqdn: postgres.outputs.fqdn
    administratorLogin: administratorLogin
    administratorLoginPassword: administratorLoginPassword
  }
}

// --- S1 model-gateway wiring (session s1-gateway) — insertion point ---
// --- fix/deploy-infra-gateway: corrected registry<->gateway dependency ---
//
// Only the registry NAME is computed here (a deterministic string — see
// container-registry.bicep's own header for why that's fine and not a
// cross-module-output problem). Everything else the gateway needs
// (loginServer, registryName-for-lookup) comes from
// containerRegistry.outputs directly, which is what gives Bicep a genuine,
// one-directional dependsOn: containerRegistry is declared first and has no
// input from gateway at all, so ARM always provisions the registry (and its
// DNS record) before attempting to provision the Container App that pulls
// from it. See gateway.bicep's and container-registry.bicep's header
// comments for the full incident writeup (deploy-infra #10's
// "failed to resolve registry ... no such host").
var containerRegistryName = 'acrcmosshared${uniqueString(resourceGroup().id)}'

module containerRegistry 'modules/container-registry.bicep' = {
  name: 'container-registry'
  params: {
    location: location
    registryName: containerRegistryName
  }
}

@description('Model-gateway container image reference. deploy-infra.yml\'s preflight resolves this to the app\'s CURRENT live image if ca-model-gateway already exists, or a public placeholder on first-ever bootstrap — see gateway.bicep\'s header comment. Only deploy-gateway.yml (via `az containerapp update --image`) ever sets a real gateway image; this default is a documentation fallback for a direct `az deployment group create` run without that preflight step (e.g. local what-if).')
param gatewayContainerImage string = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

module gateway 'modules/gateway.bicep' = {
  name: 'gateway'
  params: {
    location: location
    // environmentId, keyVaultName, postgresFqdn, and the two
    // containerRegistry.outputs references below already make this depend
    // on containerAppsEnvironment, keyVault, postgres, and containerRegistry
    // — no explicit dependsOn needed.
    environmentId: containerAppsEnvironment.outputs.environmentId
    keyVaultName: keyVault.outputs.vaultName
    postgresFqdn: postgres.outputs.fqdn
    administratorLogin: administratorLogin
    administratorLoginPassword: administratorLoginPassword
    containerRegistryLoginServer: containerRegistry.outputs.loginServer
    containerRegistryName: containerRegistry.outputs.registryName
    containerImage: gatewayContainerImage
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
