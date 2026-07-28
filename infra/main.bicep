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
//     -> vault  (postgres, container-apps-environment, key-vault, storage,
//                container-registry — all via output references; see
//                infra/modules/vault/main.bicep for its 7 child modules)
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

// -- session/s2-vault: begin --
// Vault service image tag (pushed by .github/workflows/vault-image.yml,
// normally a commit SHA); see the vault module block below.
@description('Vault service image tag to deploy.')
param vaultImageTag string = 'latest'
// -- session/s2-vault: end --

// Loaded once here (single ../, since main.bicep sits at infra/main.bicep,
// one level below repo root, same level as /contracts) and threaded down
// as a plain parameter — child modules never call loadTextContent
// themselves.
var vaultSchemaSql = loadTextContent('../contracts/vault-schema/schema.sql')

// -- session/s2-vault: begin --
// Same loadTextContent convention as vaultSchemaSql above, for the
// vault_internal sidecar migration (services/vault/migrations/
// 0001_vault_internal_init.sql) — threaded down as a plain parameter to
// infra/modules/vault/main.bicep, which never calls loadTextContent
// itself (see infra/modules/vault/sidecar-migration-job.bicep header).
var vaultInternalMigrationSql = loadTextContent('../services/vault/migrations/0001_vault_internal_init.sql')
// -- session/s2-vault: end --

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

// ---------------------------------------------------------------------
// S4 GOVERNANCE — APPEND-ONLY INSERTION POINT (begin)
//
// Everything between this marker and its matching (end) marker was ADDED
// by session S4. Nothing above or below it was modified or reordered, and
// no new top-level parameter was introduced: every module below consumes
// only existing main.bicep module outputs and existing parameters, so
// infra/dev.parameters.json needs no change.
//
// Dependency order inside this block:
//   governanceMigrationJob   (postgres, containerAppsEnvironment)
//   governanceSigningKey     (keyVault)
//   gatekeeperApprovalApp    (containerAppsEnvironment, governanceSigningKey)
//   gatekeeperApp            (… + gatekeeperApprovalApp, for APPROVAL_BASE_URL)
//   publisherApp             (containerAppsEnvironment, governanceSigningKey)
//   governanceSmokeTestJob   (gatekeeperApp, publisherApp)
// ---------------------------------------------------------------------

// Governance schema DDL, loaded here (never inside the child module) to
// match the convention migration-job.bicep established.
var governanceMigrationSql = loadTextContent('modules/governance/migrations/0001_governance_init.sql')

// Forces a fresh Container Apps revision on gatekeeperApp,
// gatekeeperApprovalApp and publisherApp on EVERY deploy. Confirmed live
// (round-3 deploy-governance failure): with activeRevisionsMode Single,
// redeploying with only a changed SECRET VALUE (administratorLoginPassword
// rotates every workflow run, per the "fresh Postgres admin password" step
// in deploy-governance.yml) does NOT create a new revision — the already
// running replica keeps the DATABASE_URL it booted with, permanently, even
// after the live Postgres password has actually changed underneath it.
// utcNow() is only valid as a parameter default in the template that is
// the root of the deployment operation, which main.bicep is — the module
// files receive this as a plain required param and use
// uniqueString(deployToken) to build their revisionSuffix.
@description('Deployment-time token threaded into every governance Container App to force a fresh revision each deploy. Defaults to utcNow(), evaluated once per `az deployment group create`/`what-if` run.')
param governanceDeployToken string = utcNow()

// The ONE unpack/launch script per service. These files are the single
// source of truth for bootstrap behaviour and are also executed verbatim
// by scripts/verify_governance_bundle_reconstruction.py.
var gatekeeperUnpackScript = loadTextContent('modules/governance/gatekeeper-bundle-unpack.sh')
var publisherUnpackScript = loadTextContent('modules/governance/publisher-bundle-unpack.sh')

// Source bundles: exactly one loadTextContent per line of each service's
// BUNDLE_MANIFEST.txt, in manifest order. Adding a runtime file means
// adding a manifest line AND a line here — the verification script fails
// loudly if the two ever disagree.
var gatekeeperBundle = {
  'requirements.txt': loadTextContent('../services/gatekeeper/requirements.txt')
  'main.py': loadTextContent('../services/gatekeeper/main.py')
  'approval_main.py': loadTextContent('../services/gatekeeper/approval_main.py')
  'policy/autonomy.yaml': loadTextContent('../services/gatekeeper/policy/autonomy.yaml')
  'app/__init__.py': loadTextContent('../services/gatekeeper/app/__init__.py')
  'app/config.py': loadTextContent('../services/gatekeeper/app/config.py')
  'app/db.py': loadTextContent('../services/gatekeeper/app/db.py')
  'app/policy_loader.py': loadTextContent('../services/gatekeeper/app/policy_loader.py')
  'app/kill_switch.py': loadTextContent('../services/gatekeeper/app/kill_switch.py')
  'app/tokens.py': loadTextContent('../services/gatekeeper/app/tokens.py')
  'app/teams_client.py': loadTextContent('../services/gatekeeper/app/teams_client.py')
  'app/approval_inbox.py': loadTextContent('../services/gatekeeper/app/approval_inbox.py')
  'app/auth.py': loadTextContent('../services/gatekeeper/app/auth.py')
  'app/signer/__init__.py': loadTextContent('../services/gatekeeper/app/signer/__init__.py')
  'app/signer/base.py': loadTextContent('../services/gatekeeper/app/signer/base.py')
  'app/signer/local_signer.py': loadTextContent('../services/gatekeeper/app/signer/local_signer.py')
  'app/signer/keyvault_signer.py': loadTextContent('../services/gatekeeper/app/signer/keyvault_signer.py')
  'app/routers/__init__.py': loadTextContent('../services/gatekeeper/app/routers/__init__.py')
  'app/routers/gate_check.py': loadTextContent('../services/gatekeeper/app/routers/gate_check.py')
  'app/routers/decisions.py': loadTextContent('../services/gatekeeper/app/routers/decisions.py')
  'app/routers/approval_action.py': loadTextContent('../services/gatekeeper/app/routers/approval_action.py')
}

var publisherBundle = {
  'requirements.txt': loadTextContent('../services/publisher/requirements.txt')
  'main.py': loadTextContent('../services/publisher/main.py')
  'app/__init__.py': loadTextContent('../services/publisher/app/__init__.py')
  'app/config.py': loadTextContent('../services/publisher/app/config.py')
  'app/db.py': loadTextContent('../services/publisher/app/db.py')
  'app/kill_switch.py': loadTextContent('../services/publisher/app/kill_switch.py')
  'app/verifier.py': loadTextContent('../services/publisher/app/verifier.py')
  'app/jti_ledger.py': loadTextContent('../services/publisher/app/jti_ledger.py')
  'app/hashing.py': loadTextContent('../services/publisher/app/hashing.py')
  'app/vault_adapter.py': loadTextContent('../services/publisher/app/vault_adapter.py')
  'app/models.py': loadTextContent('../services/publisher/app/models.py')
  'app/routers/__init__.py': loadTextContent('../services/publisher/app/routers/__init__.py')
  'app/routers/publish.py': loadTextContent('../services/publisher/app/routers/publish.py')
  'app/routers/publish_attempts.py': loadTextContent('../services/publisher/app/routers/publish_attempts.py')
}

var governanceDatabaseUrl = 'postgresql://${administratorLogin}:${administratorLoginPassword}@${postgres.outputs.fqdn}:5432/postgres?sslmode=require'

module governanceMigrationJob 'modules/governance/governance-migration-job.bicep' = {
  name: 'governance-migration-job'
  params: {
    location: location
    environmentId: containerAppsEnvironment.outputs.environmentId
    postgresFqdn: postgres.outputs.fqdn
    administratorLogin: administratorLogin
    administratorLoginPassword: administratorLoginPassword
    migrationSql: governanceMigrationSql
  }
  dependsOn: [
    postgres
    containerAppsEnvironment
  ]
}

module governanceSigningKey 'modules/governance/signing-key.bicep' = {
  name: 'governance-signing-key'
  params: {
    location: location
    keyVaultName: keyVault.outputs.vaultName
  }
  dependsOn: [
    keyVault
  ]
}

module gatekeeperApprovalApp 'modules/governance/gatekeeper-approval-app.bicep' = {
  name: 'gatekeeper-approval-app'
  params: {
    location: location
    environmentId: containerAppsEnvironment.outputs.environmentId
    bundleJson: string(gatekeeperBundle)
    unpackScript: gatekeeperUnpackScript
    userAssignedIdentityId: governanceSigningKey.outputs.gatekeeperIdentityId
    userAssignedClientId: governanceSigningKey.outputs.gatekeeperClientId
    databaseUrl: governanceDatabaseUrl
    keyVaultUri: governanceSigningKey.outputs.keyVaultUri
    signingKeyName: governanceSigningKey.outputs.signingKeyName
    deployToken: governanceDeployToken
  }
  dependsOn: [
    containerAppsEnvironment
    governanceSigningKey
    governanceMigrationJob
  ]
}

module gatekeeperApp 'modules/governance/gatekeeper-app.bicep' = {
  name: 'gatekeeper-app'
  params: {
    location: location
    environmentId: containerAppsEnvironment.outputs.environmentId
    bundleJson: string(gatekeeperBundle)
    unpackScript: gatekeeperUnpackScript
    userAssignedIdentityId: governanceSigningKey.outputs.gatekeeperIdentityId
    userAssignedClientId: governanceSigningKey.outputs.gatekeeperClientId
    databaseUrl: governanceDatabaseUrl
    keyVaultUri: governanceSigningKey.outputs.keyVaultUri
    signingKeyName: governanceSigningKey.outputs.signingKeyName
    approvalBaseUrl: gatekeeperApprovalApp.outputs.approvalBaseUrl
    deployToken: governanceDeployToken
  }
  dependsOn: [
    containerAppsEnvironment
    governanceSigningKey
    governanceMigrationJob
  ]
}

module publisherApp 'modules/governance/publisher-app.bicep' = {
  name: 'publisher-app'
  params: {
    location: location
    environmentId: containerAppsEnvironment.outputs.environmentId
    bundleJson: string(publisherBundle)
    unpackScript: publisherUnpackScript
    userAssignedIdentityId: governanceSigningKey.outputs.publisherIdentityId
    userAssignedClientId: governanceSigningKey.outputs.publisherClientId
    databaseUrl: governanceDatabaseUrl
    keyVaultUri: governanceSigningKey.outputs.keyVaultUri
    signingKeyName: governanceSigningKey.outputs.signingKeyName
    deployToken: governanceDeployToken
  }
  dependsOn: [
    containerAppsEnvironment
    governanceSigningKey
    governanceMigrationJob
  ]
}

module governanceSmokeTestJob 'modules/governance/governance-smoke-test-job.bicep' = {
  name: 'governance-smoke-test-job'
  params: {
    location: location
    environmentId: containerAppsEnvironment.outputs.environmentId
    gatekeeperFqdn: gatekeeperApp.outputs.internalFqdn
    publisherFqdn: publisherApp.outputs.internalFqdn
    postgresFqdn: postgres.outputs.fqdn
    administratorLogin: administratorLogin
    administratorLoginPassword: administratorLoginPassword
  }
  dependsOn: [
    gatekeeperApp
    publisherApp
    governanceMigrationJob
  ]
}
// S4 GOVERNANCE — APPEND-ONLY INSERTION POINT (end)
// ---------------------------------------------------------------------

// -- session/s2-vault: begin --
// Vault service — CRUD/taxonomy/consent/retention/utilisation-rollup
// over the 9 Vault object types (contracts/vault-api.yaml). Sidecar
// bookkeeping lives in a new `vault_internal` Postgres schema, never in
// the frozen public schema above. See infra/modules/vault/main.bicep for
// the 7 child modules this orchestrates.
//
// Reuses the SAME containerRegistry module instance gateway consumes
// above (module.bicep only ever declares one Microsoft.ContainerRegistry
// resource — see container-registry.bicep's header, "THIS IS THE SINGLE
// CANONICAL SHARED REGISTRY"). Vault used to author its own second
// `containerRegistry` module block here, which duplicated the symbol name
// and would have provisioned a second ACR under the pre-convention
// `acrcmosdev...` naming — dropped on rebase in favor of the one true
// instance above.
//
// vaultDeployToken follows the same governance-round-4 pattern as
// governanceDeployToken above: ca-vault runs activeRevisionsMode Single
// too, so a redeploy that only changes a secret VALUE (Postgres password
// rotation) would otherwise never create a new revision and would leave
// the running replica on a stale DATABASE_URL — see container-app.bicep's
// header for the ca-vault-specific version of this comment.
@description('Deployment-time token threaded into ca-vault to force a fresh Container Apps revision each deploy, same pattern/reasoning as governanceDeployToken. Defaults to utcNow(), evaluated once per `az deployment group create`/`what-if` run.')
param vaultDeployToken string = utcNow()

module vault 'modules/vault/main.bicep' = {
  name: 'vault'
  params: {
    location: location
    // environmentId, postgresFqdn, keyVaultName/Id, storageAccountName/Id,
    // and the three containerRegistry.outputs references below already
    // make this depend on containerAppsEnvironment, postgres, keyVault,
    // storage, and containerRegistry — no explicit dependsOn needed (see
    // this file's DEPENDSON POLICY comment above).
    environmentId: containerAppsEnvironment.outputs.environmentId
    postgresFqdn: postgres.outputs.fqdn
    administratorLogin: administratorLogin
    administratorLoginPassword: administratorLoginPassword
    migrationSql: vaultInternalMigrationSql
    keyVaultName: keyVault.outputs.vaultName
    keyVaultId: keyVault.outputs.vaultId
    storageAccountName: storage.outputs.storageAccountName
    storageAccountId: storage.outputs.storageAccountId
    acrLoginServer: containerRegistry.outputs.loginServer
    acrRegistryName: containerRegistry.outputs.registryName
    acrRegistryId: containerRegistry.outputs.registryId
    vaultImageTag: vaultImageTag
    deployToken: vaultDeployToken
  }
}
// -- session/s2-vault: end --

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

// S4 GOVERNANCE — appended outputs (append-only; no existing output line
// above was modified or reordered).
output governanceMigrationJobName string = governanceMigrationJob.outputs.jobName
output governanceSigningKeyName string = governanceSigningKey.outputs.signingKeyName
output gatekeeperAppName string = gatekeeperApp.outputs.appName
output gatekeeperInternalFqdn string = gatekeeperApp.outputs.internalFqdn
output gatekeeperApprovalAppName string = gatekeeperApprovalApp.outputs.appName
output gatekeeperApprovalBaseUrl string = gatekeeperApprovalApp.outputs.approvalBaseUrl
output publisherAppName string = publisherApp.outputs.appName
output publisherInternalFqdn string = publisherApp.outputs.internalFqdn
output governanceSmokeTestJobName string = governanceSmokeTestJob.outputs.jobName

// -- session/s2-vault: begin --
output vaultContainerAppName string = vault.outputs.containerAppName
output vaultContainerAppInternalFqdn string = vault.outputs.containerAppInternalFqdn
output vaultSidecarMigrationJobName string = vault.outputs.sidecarMigrationJobName
output vaultSecretWriterJobName string = vault.outputs.secretWriterJobName
output vaultRetentionExpiryJobName string = vault.outputs.retentionExpiryJobName
output vaultSmokeTestJobName string = vault.outputs.smokeTestJobName
// -- session/s2-vault: end --
