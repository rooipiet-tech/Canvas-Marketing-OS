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

// === CONSOLE MODULE INSERTION POINT — append new module blocks below this
// line; never edit anything above it (INFRA-001 append-only rule) ===

@description('Required Entra App Registration client id for the console\'s Easy Auth authConfig. No default — the App Registration + Federated Identity Credential are created once, manually, by a human with directory admin rights (AUTH-003); see scripts/bootstrap-console-auth.sh and docs/console-auth-runbook.md.')
param consoleClientId string

// consoleIdentity is a genuine CREATE (never an `existing =` lookup) — this
// eliminates the identity chicken-and-egg hazard architecturally: its
// principalId/clientId outputs are available within this SAME atomic
// deployment to both containerRegistryForConsole and consoleApp below, with
// no preflight, no separate bootstrap job, and no CI `needs:` ordering.
module consoleIdentity 'modules/console/console-identity.bicep' = {
  name: 'console-identity'
  params: {
    location: location
  }
}

// Consumes the ONE shared, canonical ACR module a SECOND time — this is
// the module's own documented multi-consumer pattern (see infra/modules/
// container-registry.bicep's header comment: "Any other service that
// needs to push or pull images... MUST consume this module and pass its
// own service principal id via pullPrincipalId, rather than authoring a
// second Microsoft.ContainerRegistry resource").
//
// CRITICAL: registryName MUST be `containerRegistryName` (the SAME
// variable the existing `containerRegistry` module above already uses),
// never a separately hardcoded literal. The ACR resource's actual ARM
// identity is its `name` property (= registryName), not the Bicep module
// invocation's own `name:` field — two module blocks with the SAME
// registryName both target the one real resource (idempotently adding
// this identity's AcrPull role assignment to it); two module blocks with
// DIFFERENT registryName values create two REAL, separate
// Microsoft.ContainerRegistry/registries resources, silently violating
// the one-shared-ACR rule despite every comment nearby claiming
// otherwise. (This session's build v2 originally hardcoded the literal
// 'acrcmosdevdziw5kptw2qee' here — the name confirmed live via `az`
// earlier in this session — which genuinely differs from
// `containerRegistryName`'s computed value and would have created a
// second registry on deploy; caught by tester re-review and fixed here.
// Reconciling why the live ACR's name doesn't match
// `containerRegistryName`'s current formula is a pre-existing
// s1-gateway-owned concern, out of this session's append-only scope —
// this fix only ensures THIS session's own addition can't itself create
// a duplicate.)
module containerRegistryForConsole 'modules/container-registry.bicep' = {
  name: 'container-registry-console'
  params: {
    registryName: containerRegistryName
    pullPrincipalId: consoleIdentity.outputs.principalId
  }
}

module consoleAppInsights 'modules/console/app-insights.bicep' = {
  name: 'console-app-insights'
  params: {
    logAnalyticsWorkspaceId: containerAppsEnvironment.outputs.logAnalyticsWorkspaceId
  }
}

@description('Console container image reference. deploy-infra.yml\'s preflight resolves this to the app\'s CURRENT live image if ca-console already exists, or a public placeholder on first-ever bootstrap — see console-app.bicep\'s MAIDEN-DEPLOY INCIDENT header comment (ca-console\'s registries[].identity/registry-pull identity is set exclusively via deploy-console.yml\'s `az containerapp registry set`, never by this template, to sidestep a confirmed Azure platform limitation on first create). This default is a documentation fallback for a direct `az deployment group create` run without that preflight step (e.g. local what-if).')
param consoleContainerImage string = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

module consoleApp 'modules/console/console-app.bicep' = {
  name: 'console-app'
  params: {
    location: location
    environmentId: containerAppsEnvironment.outputs.environmentId
    containerImage: consoleContainerImage
    consoleClientId: consoleClientId
    tenantId: subscription().tenantId
    consoleIdentityId: consoleIdentity.outputs.id
    consoleIdentityClientId: consoleIdentity.outputs.clientId
    applicationInsightsConnectionString: consoleAppInsights.outputs.connectionString
    // L-0025: resolve the console's INTEG-001/INTEG-002 switch-to-real base
    // URLs from ca-vault's and ca-gatekeeper's own live internalFqdn
    // outputs, never a hardcoded aspirational hostname — mirrors vault/
    // main.bicep's own `'https://${containerApp.outputs.internalFqdn}'`
    // pattern (line ~177). VAULT_API_MODE/GATEKEEPER_API_MODE stay hardcoded
    // 'mock' in console-app.bicep (real integration deferred to S8, per
    // owner ruling) — only the base URL is wired real today, so that
    // deferred switch is a pure env-var flip with no infra follow-up.
    vaultApiBaseUrl: 'https://${vault.outputs.containerAppInternalFqdn}'
    gatekeeperApiBaseUrl: 'https://${gatekeeperApp.outputs.internalFqdn}'
  }
  dependsOn: [
    containerAppsEnvironment
    containerRegistryForConsole
    consoleAppInsights
    consoleIdentity
  ]
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
// PATCH (chicken-and-egg AcrPull ordering fix — see
// infra/modules/vault/managed-identity.bicep's header): the
// user-assigned identity ca-vault / caj-vault-retention-expiry /
// caj-vault-smoke-test all pull their shared-ACR image with.
output vaultManagedIdentityId string = vault.outputs.managedIdentityId
output vaultContainerAppName string = vault.outputs.containerAppName
output vaultContainerAppInternalFqdn string = vault.outputs.containerAppInternalFqdn
output vaultSidecarMigrationJobName string = vault.outputs.sidecarMigrationJobName
output vaultSecretWriterJobName string = vault.outputs.secretWriterJobName
output vaultRetentionExpiryJobName string = vault.outputs.retentionExpiryJobName
output vaultSmokeTestJobName string = vault.outputs.smokeTestJobName
// -- session/s2-vault: end --

// ---------------------------------------------------------------------
// session/s3-orchestrator: begin (append-only additions, C10 — every
// line above this point is byte-identical to origin/main)
// ---------------------------------------------------------------------

var orchestratorMigrationSql = loadTextContent('../services/orchestrator/migrations/0001_orchestrator_init.sql')

// INCIDENT (deploy-infra run 30624109154, 2026-07-31): this used to be a
// Bicep-computed `'${containerRegistry.outputs.loginServer}/orchestrator:latest'`
// var, with nothing ever guaranteeing that tag existed in the registry
// before the FIRST deploy that referenced it. It didn't: ca-orchestrator
// failed to provision with ContainerAppOperationError/MANIFEST_UNKNOWN
// ("manifest tagged by \"latest\" is not found") because orchestrator-image.yml's
// build had pushed to a different, orphaned registry (see that workflow's
// own header for the L-0021-class fix) — so even a successful image build
// never reached the registry this template actually points at.
//
// Same fix as gatewayContainerImage above (see that param's header and
// deploy-infra.yml's "Resolve gateway container image (preserve live
// image, bootstrap with placeholder)" step, which this mirrors exactly
// for ca-orchestrator): default to a public, unauthenticated MCR
// quickstart image that needs no dependency on this repo's own registry
// at all, so first-ever provisioning of ca-orchestrator can never fail on
// a missing/wrong-registry image. deploy-infra.yml's preflight resolves
// this to ca-orchestrator's CURRENT live image once one has ever gone
// Ready — never back to this placeholder afterward, and never to a
// literal `:latest` this template computes itself. The only thing that
// ever sets a REAL orchestrator image is orchestrator-image.yml's gated
// `deploy` job (`az containerapp update --image ...:<commit-sha>`, pinned
// to the building commit's SHA, never `:latest` — same reasoning as
// deploy-gateway.yml's image tag, C11).
//
// F3: the single shared image reference — passed unmodified to BOTH
// orchestratorContainerApp and orchestratorSmokeTestJob below, never
// re-derived independently per module.
@description('Orchestrator service container image reference. deploy-infra.yml\'s preflight resolves this to the app\'s CURRENT live image if ca-orchestrator already has a Ready revision, or this public placeholder on first-ever bootstrap — see gateway.bicep\'s/gatewayContainerImage\'s identical pattern above. Only orchestrator-image.yml\'s gated deploy job (via `az containerapp update --image`) ever sets a real, SHA-pinned orchestrator image; this default is a documentation fallback for a direct `az deployment group create` run without that preflight step (e.g. local what-if).')
param orchestratorContainerImage string = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

// REBASE FIX (post-main-rebase, L-0020 class): does NOT redeclare a second
// `containerRegistry` module — the root module above (S1 gateway wiring,
// ~line 178) already declares the one true instance this repo's whole
// platform shares. This block only ever reads containerRegistry.outputs.*.

// Chicken-and-egg AcrPull ordering fix (L-0020, same bug/fix as
// infra/modules/vault/managed-identity.bicep): a SystemAssigned identity's
// principalId only exists once its owning Container App/Job has actually
// been created, but that same resource needs an AcrPull grant on that
// principalId to pull its image AT creation time — so granting AcrPull to
// orchestratorContainerApp/orchestratorSmokeTestJob's own SystemAssigned
// identity in the same deployment deadlocks (confirmed live for ca-vault
// under the identical pattern: 20+ min "Operation expired", zero role
// assignments ever created). id-orchestrator is a UserAssignedIdentity
// provisioned here with NO dependency on either consumer, granted AcrPull
// independently and ahead of time, then referenced by both.
module orchestratorIdentity 'modules/orchestrator/managed-identity.bicep' = {
  name: 'orchestrator-managed-identity'
  params: {
    location: location
    acrRegistryName: containerRegistry.outputs.registryName
    acrRegistryId: containerRegistry.outputs.registryId
  }
}

// Governance-round-4 revisionSuffix pattern (same as governanceDeployToken/
// vaultDeployToken above): ca-orchestrator runs activeRevisionsMode Single,
// so a redeploy that only changes a secret VALUE (e.g. a rotated Postgres
// admin password) would not otherwise create a new revision, leaving the
// running replica on a stale DATABASE_URL. Defaults to utcNow(), evaluated
// once per `az deployment group create`/`what-if` run.
param orchestratorDeployToken string = utcNow()

module orchestratorContainerApp 'modules/orchestrator/container-app.bicep' = {
  name: 'orchestrator-container-app'
  params: {
    location: location
    environmentId: containerAppsEnvironment.outputs.environmentId
    acrLoginServer: containerRegistry.outputs.loginServer
    acrRegistryName: containerRegistry.outputs.registryName
    acrRegistryId: containerRegistry.outputs.registryId
    userAssignedIdentityId: orchestratorIdentity.outputs.identityId
    orchestratorImage: orchestratorContainerImage
    postgresFqdn: postgres.outputs.fqdn
    administratorLogin: administratorLogin
    administratorLoginPassword: administratorLoginPassword
    serviceBusNamespaceName: serviceBus.outputs.namespaceName
    deployToken: orchestratorDeployToken
  }
  dependsOn: [
    orchestratorIdentity
    containerAppsEnvironment
    serviceBus
    postgres
  ]
}

module orchestratorMigrationJob 'modules/orchestrator/migration-job.bicep' = {
  name: 'orchestrator-migration-job'
  params: {
    location: location
    environmentId: containerAppsEnvironment.outputs.environmentId
    postgresFqdn: postgres.outputs.fqdn
    administratorLogin: administratorLogin
    administratorLoginPassword: administratorLoginPassword
    migrationSql: orchestratorMigrationSql
  }
  dependsOn: [
    postgres
    containerAppsEnvironment
  ]
}

module orchestratorSmokeTestJob 'modules/orchestrator/smoke-test-job.bicep' = {
  name: 'orchestrator-smoke-test-job'
  params: {
    location: location
    environmentId: containerAppsEnvironment.outputs.environmentId
    acrLoginServer: containerRegistry.outputs.loginServer
    acrRegistryName: containerRegistry.outputs.registryName
    acrRegistryId: containerRegistry.outputs.registryId
    userAssignedIdentityId: orchestratorIdentity.outputs.identityId
    orchestratorImage: orchestratorContainerImage
    orchestratorStatusUrl: 'http://${orchestratorContainerApp.outputs.internalFqdn}/status'
    serviceBusNamespaceName: serviceBus.outputs.namespaceName
  }
  dependsOn: [
    orchestratorContainerApp
    orchestratorMigrationJob
  ]
}

module scheduling 'modules/scheduling/logic-apps.bicep' = {
  name: 'scheduling'
  params: {
    location: location
    serviceBusNamespaceName: serviceBus.outputs.namespaceName
  }
  dependsOn: [
    serviceBus
  ]
}

output orchestratorAppName string = orchestratorContainerApp.outputs.appName
output orchestratorManagedIdentityId string = orchestratorIdentity.outputs.identityId
output orchestratorMigrationJobName string = orchestratorMigrationJob.outputs.jobName
output orchestratorSmokeTestJobName string = orchestratorSmokeTestJob.outputs.jobName

// ---------------------------------------------------------------------
// session/s3-orchestrator: end
// ---------------------------------------------------------------------

output consoleAppFqdn string = consoleApp.outputs.fqdn
output applicationInsightsName string = consoleAppInsights.outputs.name
output consoleIdentityPrincipalId string = consoleIdentity.outputs.principalId

// ---------------------------------------------------------------------
// MCP MODULES INSERTION POINT (session/s5-mcp) — begin
//
// Everything between this marker and its matching (end) marker is an
// append-only addition (ruling R2 / AC-18): the MCP tool-plane's
// Container Apps, their per-app managed identities, Key Vault Secrets
// User + AcrPull role assignments, the mcp_ops schema migration job, and
// the in-VNet conformance smoke job. No line above this marker is
// modified or reordered.
//
// REBASE FIX (post-main-rebase): does NOT redeclare a second
// `containerRegistry` module — the root module above (S1 gateway wiring,
// ~line 178) already declares the one true shared instance; this block
// only ever reads containerRegistry.outputs.* (same pattern session/s2
// -vault and session/s3-orchestrator already follow).
//
// Dependency graph for this block:
//   containerRegistry (existing, above) -> { id-mcp-web, id-mcp-buffer,
//     id-mcp-canva } -> { key-vault-role-assignment x3, acr-role
//     -assignment x3 } -> { mcp-web/-buffer/-canva container apps }
//     -> mcp-smoke-job (depends on all 3 apps + reuses id-mcp-web's
//        already-granted AcrPull for its own image pull)
//   mcp-ops-migrate-job depends only on postgres + containerAppsEnvironment,
//   same as migrationJob/vaultQueryJob above.
// ---------------------------------------------------------------------

var mcpOpsSchemaSql = loadTextContent('../mcp/mcp_ops/schema.sql')

var bufferApiKeyUrl = 'https://${keyVault.outputs.vaultName}.vault.azure.net/secrets/buffer-api-key'
var canvaClientIdUrl = 'https://${keyVault.outputs.vaultName}.vault.azure.net/secrets/canva-client-id'
var canvaClientSecretUrl = 'https://${keyVault.outputs.vaultName}.vault.azure.net/secrets/canva-client-secret'

// Governance-round-4 revisionSuffix pattern (same as governanceDeployToken/
// vaultDeployToken/orchestratorDeployToken above): the 3 mcp-* Container
// Apps run activeRevisionsMode Single, so a redeploy that only changes a
// secret VALUE (rotated Postgres admin password, or an updated Key Vault
// secret VERSION for mcp-buffer/mcp-canva) would not otherwise create a
// new revision, leaving the running replica on stale secret values.
// Defaults to utcNow(), evaluated once per `az deployment group create`/
// `what-if` run.
@description('Deployment-time token threaded into every mcp-* Container App to force a fresh revision each deploy, same pattern/reasoning as governanceDeployToken/vaultDeployToken/orchestratorDeployToken. Defaults to utcNow(), evaluated once per `az deployment group create`/`what-if` run.')
param mcpDeployToken string = utcNow()

module idMcpWeb 'modules/mcp/identity.bicep' = {
  name: 'id-mcp-web'
  params: {
    location: location
    identityName: 'id-mcp-web'
  }
}

module idMcpBuffer 'modules/mcp/identity.bicep' = {
  name: 'id-mcp-buffer'
  params: {
    location: location
    identityName: 'id-mcp-buffer'
  }
}

module idMcpCanva 'modules/mcp/identity.bicep' = {
  name: 'id-mcp-canva'
  params: {
    location: location
    identityName: 'id-mcp-canva'
  }
}

// Key Vault Secrets User role assignments — all 3 identities, INCLUDING
// mcp-web's (plan v3 F5-REGRESSION fix: mcp-web never actually calls Key
// Vault, but AC-11's frozen "every mcp-* Container App's identity" text
// requires the grant to exist regardless; an accepted, harmless, unused
// residual per the plan's risk register).
module mcpWebKvRole 'modules/mcp/key-vault-role-assignment.bicep' = {
  name: 'mcp-web-kv-role'
  params: {
    keyVaultName: keyVault.outputs.vaultName
    principalId: idMcpWeb.outputs.principalId
  }
  dependsOn: [
    keyVault
    idMcpWeb
  ]
}

module mcpBufferKvRole 'modules/mcp/key-vault-role-assignment.bicep' = {
  name: 'mcp-buffer-kv-role'
  params: {
    keyVaultName: keyVault.outputs.vaultName
    principalId: idMcpBuffer.outputs.principalId
  }
  dependsOn: [
    keyVault
    idMcpBuffer
  ]
}

module mcpCanvaKvRole 'modules/mcp/key-vault-role-assignment.bicep' = {
  name: 'mcp-canva-kv-role'
  params: {
    keyVaultName: keyVault.outputs.vaultName
    principalId: idMcpCanva.outputs.principalId
  }
  dependsOn: [
    keyVault
    idMcpCanva
  ]
}

// AcrPull role assignments — all 3 identities need to pull their own image.
module mcpWebAcrRole 'modules/mcp/acr-role-assignment.bicep' = {
  name: 'mcp-web-acr-role'
  params: {
    registryName: containerRegistry.outputs.registryName
    principalId: idMcpWeb.outputs.principalId
  }
  dependsOn: [
    containerRegistry
    idMcpWeb
  ]
}

module mcpBufferAcrRole 'modules/mcp/acr-role-assignment.bicep' = {
  name: 'mcp-buffer-acr-role'
  params: {
    registryName: containerRegistry.outputs.registryName
    principalId: idMcpBuffer.outputs.principalId
  }
  dependsOn: [
    containerRegistry
    idMcpBuffer
  ]
}

module mcpCanvaAcrRole 'modules/mcp/acr-role-assignment.bicep' = {
  name: 'mcp-canva-acr-role'
  params: {
    registryName: containerRegistry.outputs.registryName
    principalId: idMcpCanva.outputs.principalId
  }
  dependsOn: [
    containerRegistry
    idMcpCanva
  ]
}

module mcpWebApp 'modules/mcp/container-app.bicep' = {
  name: 'mcp-web-app'
  params: {
    location: location
    appName: 'mcp-web'
    environmentId: containerAppsEnvironment.outputs.environmentId
    image: '${containerRegistry.outputs.loginServer}/mcp-web:latest'
    registryLoginServer: containerRegistry.outputs.loginServer
    userAssignedIdentityId: idMcpWeb.outputs.identityId
    targetPort: 8080
    envVars: [
      {
        name: 'MCP_WEB_ALLOWLIST'
        value: 'example.com,api.example.com'
      }
    ]
    keyVaultSecretRefs: []
    deployToken: mcpDeployToken
  }
  dependsOn: [
    containerAppsEnvironment
    containerRegistry
    idMcpWeb
    mcpWebAcrRole
    mcpWebKvRole
  ]
}

module mcpBufferApp 'modules/mcp/container-app.bicep' = {
  name: 'mcp-buffer-app'
  params: {
    location: location
    appName: 'mcp-buffer'
    environmentId: containerAppsEnvironment.outputs.environmentId
    image: '${containerRegistry.outputs.loginServer}/mcp-buffer:latest'
    registryLoginServer: containerRegistry.outputs.loginServer
    userAssignedIdentityId: idMcpBuffer.outputs.identityId
    targetPort: 8080
    envVars: []
    keyVaultSecretRefs: [
      {
        envName: 'BUFFER_API_KEY'
        keyVaultUrl: bufferApiKeyUrl
      }
    ]
    deployToken: mcpDeployToken
  }
  dependsOn: [
    containerAppsEnvironment
    containerRegistry
    idMcpBuffer
    mcpBufferAcrRole
    mcpBufferKvRole
  ]
}

module mcpCanvaApp 'modules/mcp/container-app.bicep' = {
  name: 'mcp-canva-app'
  params: {
    location: location
    appName: 'mcp-canva'
    environmentId: containerAppsEnvironment.outputs.environmentId
    image: '${containerRegistry.outputs.loginServer}/mcp-canva:latest'
    registryLoginServer: containerRegistry.outputs.loginServer
    userAssignedIdentityId: idMcpCanva.outputs.identityId
    targetPort: 8080
    envVars: []
    keyVaultSecretRefs: [
      {
        envName: 'CANVA_CLIENT_ID'
        keyVaultUrl: canvaClientIdUrl
      }
      {
        envName: 'CANVA_CLIENT_SECRET'
        keyVaultUrl: canvaClientSecretUrl
      }
    ]
    deployToken: mcpDeployToken
  }
  dependsOn: [
    containerAppsEnvironment
    containerRegistry
    idMcpCanva
    mcpCanvaAcrRole
    mcpCanvaKvRole
  ]
}

module mcpOpsMigrateJob 'modules/mcp/mcp-ops-migrate-job.bicep' = {
  name: 'mcp-ops-migrate-job'
  params: {
    location: location
    environmentId: containerAppsEnvironment.outputs.environmentId
    postgresFqdn: postgres.outputs.fqdn
    administratorLogin: administratorLogin
    administratorLoginPassword: administratorLoginPassword
    schemaSql: mcpOpsSchemaSql
  }
  dependsOn: [
    postgres
    containerAppsEnvironment
  ]
}

module mcpSmokeJob 'modules/mcp/mcp-smoke-job.bicep' = {
  name: 'mcp-smoke-job'
  params: {
    location: location
    environmentId: containerAppsEnvironment.outputs.environmentId
    image: '${containerRegistry.outputs.loginServer}/mcp-smoke:latest'
    registryLoginServer: containerRegistry.outputs.loginServer
    userAssignedIdentityId: idMcpWeb.outputs.identityId
    mcpWebBaseUrl: 'https://${mcpWebApp.outputs.fqdn}'
    mcpBufferBaseUrl: 'https://${mcpBufferApp.outputs.fqdn}'
    mcpCanvaBaseUrl: 'https://${mcpCanvaApp.outputs.fqdn}'
  }
  dependsOn: [
    mcpWebApp
    mcpBufferApp
    mcpCanvaApp
    mcpWebAcrRole
  ]
}

// containerRegistryLoginServer/containerRegistryName are already output
// above (S1 gateway wiring) — not re-declared here (would be a duplicate
// output-symbol error).
output mcpWebAppName string = mcpWebApp.outputs.appName
output mcpBufferAppName string = mcpBufferApp.outputs.appName
output mcpCanvaAppName string = mcpCanvaApp.outputs.appName
output mcpOpsMigrateJobName string = mcpOpsMigrateJob.outputs.jobName
output mcpSmokeJobName string = mcpSmokeJob.outputs.jobName

// ---------------------------------------------------------------------
// MCP MODULES INSERTION POINT (session/s5-mcp) — end
// ---------------------------------------------------------------------
