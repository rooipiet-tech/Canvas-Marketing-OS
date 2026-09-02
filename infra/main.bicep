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

// F-GATEWAY-SECRET-STALE-REVISION (6 Aug 2026, round 19i): same
// governance-round-4 pattern as governanceDeployToken/vaultDeployToken
// above — ca-model-gateway runs activeRevisionsMode Single too, so a
// redeploy that only changes a secret VALUE (administratorLoginPassword
// rotation) would otherwise never create a new revision, leaving the
// running replica on a stale DATABASE_URL. This was the one governance-
// round-4-era app that never got a deploy token, root-caused after two
// consecutive real heartbeat runs (#64, #65) failed with "FATAL: password
// authentication failed for user cmosadmin" from model-gateway
// specifically — see gateway.bicep's deployToken param for the
// module-local version of this comment.
@description('Deployment-time token threaded into ca-model-gateway to force a fresh Container Apps revision each deploy, same pattern/reasoning as vaultDeployToken. Defaults to utcNow(), evaluated once per `az deployment group create`/`what-if` run.')
param gatewayDeployToken string = utcNow()

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
    deployToken: gatewayDeployToken
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
  // v4 carve-out (risk-security RS-01, blocker): these 2 files are new
  // this session (GET /approval-status route + telemetry_lib wiring) and
  // were missing from both BUNDLE_MANIFEST.txt and this var — gatekeeper
  // imports them at startup, so without this the deployed bundle
  // ImportError-crash-loops. Matches BUNDLE_MANIFEST.txt's now-updated
  // order exactly. spec.json v4 amendment explicitly authorizes this
  // exact addition as a named carve-out inside this insertion-point block.
  'app/routers/approval_status.py': loadTextContent('../services/gatekeeper/app/routers/approval_status.py')
  // GET /approval-inbox (INTEG-002): the route the console has always
  // called and this service never exposed. Listed here AND in
  // BUNDLE_MANIFEST.txt -- the reconstruction check compiles the
  // unpacked bundle, so a router present in the repo but missing from
  // this map is a gatekeeper that fails to import at startup.
  'app/routers/approval_inbox_list.py': loadTextContent('../services/gatekeeper/app/routers/approval_inbox_list.py')
  'app/telemetry_wiring.py': loadTextContent('../services/gatekeeper/app/telemetry_wiring.py')
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
  // v4 carve-out (risk-security RS-01, blocker): these 3 files are new
  // this session (Buffer client, telemetry_lib wiring, Vault content-hash
  // lookup) and were missing from both BUNDLE_MANIFEST.txt and this var —
  // publisher imports them at startup, so without this the deployed
  // bundle ImportError-crash-loops. Matches BUNDLE_MANIFEST.txt's
  // now-updated order exactly. spec.json v4 amendment explicitly
  // authorizes this exact addition as a named carve-out inside this
  // insertion-point block.
  'app/buffer_client.py': loadTextContent('../services/publisher/app/buffer_client.py')
  'app/telemetry_wiring.py': loadTextContent('../services/publisher/app/telemetry_wiring.py')
  'app/vault_lookup.py': loadTextContent('../services/publisher/app/vault_lookup.py')
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
    teamsWebhookUrlKeyVaultUrl: teamsWebhookUrlSecretUrl
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
    // pattern (line ~177). Both switches have since been made, in
    // console-app.bicep and each in one line: GATEKEEPER_API_MODE='real'
    // (INTEG-002, once ca-gatekeeper gained GET /approval-inbox) and
    // VAULT_API_MODE='real' (INTEG-001). Wiring these base URLs to live
    // FQDNs from day one is what made both flips cost a line and no infra
    // follow-up — which was the point of doing it that way.
    vaultApiBaseUrl: 'https://${vault.outputs.containerAppInternalFqdn}'
    gatekeeperApiBaseUrl: 'https://${gatekeeperApp.outputs.internalFqdn}'
    // F-TEAMS-CARD-REVIEW-LINK: constructed from the shared environment's
    // defaultDomain rather than orchestratorContainerApp.outputs.internalFqdn
    // to avoid a circular module dependency — orchestratorContainerApp's own
    // cmosConsoleBaseUrl param below is built the same way, from consoleApp's
    // fixed app name, not consoleApp's own output.
    orchestratorApiBaseUrl: 'https://ca-orchestrator.${containerAppsEnvironment.outputs.defaultDomain}'
  }
  dependsOn: [
    containerAppsEnvironment
    containerRegistryForConsole
    consoleAppInsights
    consoleIdentity
  ]
}


// caj-console-smoke, declared here rather than created inline by
// deploy-console.yml's CLI call -- see console-smoke-job.bicep's header
// for the argparse bug that made every one of those eight runs fail, and
// for the L-0022 rule this restores compliance with.
module consoleSmokeJob 'modules/console/console-smoke-job.bicep' = {
  name: 'console-smoke-job'
  params: {
    location: location
    environmentId: containerAppsEnvironment.outputs.environmentId
    consoleFqdn: consoleApp.outputs.fqdn
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

// v4 carve-out (migration lens F-1, blocker): this used to load ONLY
// 0001_orchestrator_init.sql. dispatch.py/db.py now unconditionally
// depend on 0002_task_result_ref.sql's result_ref column,
// 0003_qa_blocked_reason.sql's extended CHECK constraint, and (2026-08-04,
// F-DISPATCH-CASCADE) 0004_dependency_dead_lettered_reason.sql's further
// extended CHECK constraint (adds 'dependency_dead_lettered', the reason
// state_machine.cascade_dead_letter records) at runtime, but
// caj-orchestrator-migrate (migration-job.bicep) applies exactly the
// string in this var via a single `psql -f` invocation — CI's conftest.py
// masks this gap by applying migrations/*.sql via a directory glob, which
// the real deploy pipeline does not do. Each file is self-contained
// BEGIN/COMMIT SQL (see each file's own header), so concatenating them in
// order with newline joins is safe: psql executes each BEGIN/COMMIT block
// in sequence from the one resulting file. spec.json v4 amendment
// explicitly authorizes this exact value-level change as a named
// carve-out inside this insertion-point block.
var orchestratorMigrationSql = join([
  loadTextContent('../services/orchestrator/migrations/0001_orchestrator_init.sql')
  loadTextContent('../services/orchestrator/migrations/0002_task_result_ref.sql')
  loadTextContent('../services/orchestrator/migrations/0003_qa_blocked_reason.sql')
  loadTextContent('../services/orchestrator/migrations/0004_dependency_dead_lettered_reason.sql')
], '\n')

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
    userAssignedIdentityPrincipalId: orchestratorIdentity.outputs.identityPrincipalId
    orchestratorImage: orchestratorContainerImage
    postgresFqdn: postgres.outputs.fqdn
    administratorLogin: administratorLogin
    administratorLoginPassword: administratorLoginPassword
    serviceBusNamespaceName: serviceBus.outputs.namespaceName
    // Never omitted: the module's own param was previously defaulted to
    // '' and this call site never passed it, so every deploy-infra run
    // published ca-orchestrator with VAULT_API_URL="". Empty is falsy,
    // so resolve_vault_base_url() fell through to its az-CLI fallback --
    // which does not exist inside the container -- and every handler
    // that builds a Vault client died. Same shape as the four URLs
    // below, from the same vault output already used elsewhere here.
    vaultApiUrl: 'https://${vault.outputs.containerAppInternalFqdn}'
    cmosGatewayBaseUrl: 'https://${gateway.outputs.fqdn}'
    cmosMcpWebBaseUrl: 'https://${mcpWebApp.outputs.fqdn}'
    // A3 (2 Sep 2026): the orchestrator's carousel handler now calls
    // mcp-canva's bulk_create_from_csv. Same never-omit rule as
    // vaultApiUrl above -- an empty value falls through to an az-CLI
    // lookup that does not exist inside the container.
    cmosMcpCanvaBaseUrl: 'https://${mcpCanvaApp.outputs.fqdn}'
    cmosGatekeeperBaseUrl: 'https://${gatekeeperApp.outputs.internalFqdn}'
    // F-TEAMS-CARD-REVIEW-LINK: see consoleApp module's orchestratorApiBaseUrl
    // comment above — same circular-dependency avoidance, mirrored.
    cmosConsoleBaseUrl: 'https://ca-console.${containerAppsEnvironment.outputs.defaultDomain}'
    keyVaultName: keyVault.outputs.vaultName
    teamsWebhookUrlKeyVaultUrl: teamsWebhookUrlSecretUrl
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
    orchestratorStatusUrl: 'https://${orchestratorContainerApp.outputs.internalFqdn}/status'
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

// One vault URI, four secret URLs derived from it. A3 (2 Sep 2026) needed
// the bare URI for ca-mcp-canva's KEY_VAULT_URI, and adding a fifth copy of
// the literal would have added a fifth no-hardcoded-env-urls warning
// against the ratchet. Deriving the existing four instead removes three.
// The produced strings are byte-identical: keyVaultUri ends in '/'.
var keyVaultUri = 'https://${keyVault.outputs.vaultName}.vault.azure.net/'
var bufferApiKeyUrl = '${keyVaultUri}secrets/buffer-api-key'
var canvaClientIdUrl = '${keyVaultUri}secrets/canva-client-id'
var canvaClientSecretUrl = '${keyVaultUri}secrets/canva-client-secret'
var teamsWebhookUrlSecretUrl = '${keyVaultUri}secrets/teams-webhook-url'

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

// MAIDEN-DEPLOY INCIDENT (2026-07-31): deploy-infra run failed provisioning
// mcp-web/mcp-canva (and mcp-buffer) with MANIFEST_UNKNOWN for
// `mcp-{web,buffer,canva}:latest` — the same L-0048 bootstrap gap
// ca-orchestrator hit (PR #29): these images had never been built, and a
// Bicep-computed `.../mcp-web:latest` gives ARM nothing to fall back on.
// Adopts the exact 3-part bootstrap contract (L-0048), plus its 4th part
// for a registry-pull identity (L-0049, see container-app.bicep's header):
// each image param defaults to a public MCR placeholder needing no
// registry auth at all; deploy-infra.yml's preflight resolves it to the
// app's CURRENT live image once one exists, never regressing a running
// app back to placeholder; only deploy-mcp.yml's gated deploy job (via
// `az containerapp registry set` + `az containerapp update --image`,
// pinned to the commit SHA — never `:latest`) ever sets a real image.
// mcp-web's fixture-vs-live switch. UNLIKE mcp-buffer/mcp-canva, whose
// switch is credential-presence-based (a Key Vault secret being resolvable
// IS the signal), mcp-web holds no vendor credential -- it is a
// fetch+rate-limit server -- so its switch is this plain non-secret flag,
// per the orchestrator-approved waiver recorded in mcp-web/app/tools.py's
// module docstring.
//
// F-MCP-WEB-LIVE-MODE-DRIFT: this flag was previously set BY HAND on
// ca-mcp-web (2026-08-02, evidenced in .compound/learnings/architecture/
// L-0074.md) and declared NOWHERE in this template. mcp_common's default
// for an unset flag is fixture mode, which returns the same synthetic
// body for EVERY url -- so any deploy-infra run that recreated the app's
// env vars would silently return the daily market scan to reading
// "SYNTHETIC-TEST-DATA: ..." while still reporting 23 completed tasks.
// Declaring it here makes liveness reviewable in git and survivable
// across a redeploy.
//
// Safe for caj-mcp-smoke: mcp/conftest.py sends protocol.py's
// FIXTURE_MODE_HEADER on the remote-base-url branch too, so the
// conformance suite's synthetic arguments stay fixture-backed regardless
// of what this flag is set to -- that per-request override is exactly the
// fix L-0074's follow-up landed.
@description('Whether mcp-web performs real HTTP fetches (true) or returns its checked-in synthetic fixture (false). Live fetching is what function 09\'s daily market scan reads; fixture mode returns the same synthetic body for every URL, which the orchestrator\'s distinct-domain floor now fails loudly rather than scanning.')
param mcpWebLiveMode bool = true

@description('mcp-web container image reference. deploy-infra.yml\'s preflight resolves this to the app\'s CURRENT live image if mcp-web already has a Ready revision, or this public placeholder on first-ever bootstrap — see container-app.bicep\'s identical pattern. Only deploy-mcp.yml\'s gated deploy job (via `az containerapp update --image`) ever sets a real, SHA-pinned mcp-web image.')
param mcpWebContainerImage string = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

@description('mcp-buffer container image reference. Same bootstrap pattern as mcpWebContainerImage above.')
param mcpBufferContainerImage string = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

@description('mcp-canva container image reference. Same bootstrap pattern as mcpWebContainerImage above.')
param mcpCanvaContainerImage string = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

@description('mcp-smoke test image reference (caj-mcp-smoke). Same bootstrap pattern as mcpWebContainerImage above, adapted for a one-shot Container Apps Job — deploy-infra.yml preserves the job\'s current persisted image rather than checking a "ready revision" (jobs have no revision concept). Only deploy-mcp.yml\'s gated deploy job (via `az containerapp job update --image`) ever sets a real, SHA-pinned mcp-smoke image.')
param mcpSmokeContainerImage string = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

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
    image: mcpWebContainerImage
    userAssignedIdentityId: idMcpWeb.outputs.identityId
    targetPort: 8080
    envVars: [
      {
        // WIDENED 2 Sep 2026 by owner instruction, from 3 hosts to 7, to
        // bring the competitor-discovery and fabric-ecosystem scan
        // profiles live -- the first two of the eleven written-but-
        // sourceless scanners to get sources.
        //
        // This list is a security control (AC-17), not configuration, so
        // the four added hosts are named rather than left to a diff:
        //   www.itweb.co.za          | function 10's prompt.md, SA IT trade press
        //   www.businesslive.co.za   | function 10's prompt.md, same
        //   www.etenders.gov.za      | function 10's prompt.md, National Treasury tenders
        //   techcommunity.microsoft.com | function 16's prompt.md, Fabric partner ecosystem
        //
        // All four were already on MCP_WEB_PROBE_ALLOWLIST below, so this
        // widens what may be INGESTED to what could already be MEASURED.
        // Nothing here was reached automatically: source-candidates.yaml
        // proposes, and a person decides -- which is the whole point of
        // the two-allow-list split documented on the probe list.
        //
        // The value stays an explicit host list, never a wildcard, and
        // stays in sync with functions/_shared/scan-profiles.yaml via
        // scripts/check_allowlist_sync.py (which prints this exact string
        // on drift). Removing a host here is how a source is retired.
        name: 'MCP_WEB_ALLOWLIST'
        value: 'businesstech.co.za,learn.microsoft.com,techcommunity.microsoft.com,www.businesslive.co.za,www.etenders.gov.za,www.itweb.co.za,www.moneyweb.co.za' // DE-6/AC-23/AC-17 carve-out (session/s8, step 8): real scan-profile domains (kept in sync with functions/_shared/scan-profiles.yaml by scripts/check_allowlist_sync.py) -- not a wildcard; the ONLY changed line in any of the 5 marked blocks (AC-17)
      }
      {
        name: 'MCP_WEB_LIVE_MODE'
        value: string(mcpWebLiveMode) // see mcpWebLiveMode's own comment above (F-MCP-WEB-LIVE-MODE-DRIFT)
      }
      {
        // The SOURCE-PROMOTION SANDBOX, and deliberately a different list
        // from MCP_WEB_ALLOWLIST above. A host here may be probed
        // (probe_url: status, content type, feed/item counts, extractable
        // size, five sample titles -- never the body) and may NOT be
        // fetched by a scan. Promotion from this list to the one above is
        // a human approving a gate-check card, never an automated step:
        // the scan allow-list is AC-17's egress control, not config.
        // Kept in sync with functions/_shared/source-candidates.yaml by
        // scripts/check_allowlist_sync.py.
        name: 'MCP_WEB_PROBE_ALLOWLIST'
        value: 'learn.microsoft.com,techcommunity.microsoft.com,www.businesslive.co.za,www.etenders.gov.za,www.itweb.co.za'
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
    image: mcpBufferContainerImage
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
    image: mcpCanvaContainerImage
    userAssignedIdentityId: idMcpCanva.outputs.identityId
    targetPort: 8080
    // A3 (2 Sep 2026). Was `envVars: []`, which made canva-refresh-token
    // UNREACHABLE and so made this whole integration unreachable.
    // mcp_common.resolve_secret finds a secret two ways: the env var, or a
    // Key Vault SDK lookup gated on KEY_VAULT_URI. The two Canva secrets
    // below arrive as Container Apps secretRefs, but the refresh token
    // cannot: it does not exist in Key Vault yet, and a secretRef to a
    // missing secret fails the revision, so every deploy would break until
    // someone minted one.
    //
    // KEY_VAULT_URI is the path that degrades correctly instead --
    // resolve_secret returns None and the server stays in fixture mode
    // until the secret appears, then picks it up on the next restart with
    // no infra change. Same pattern as analytics' nightly-ingest-job and
    // buffer-smoke-job.
    //
    // AZURE_CLIENT_ID is not optional here: ca-mcp-canva has ONLY a
    // user-assigned identity, and DefaultAzureCredential will not select
    // one without being told which. id-mcp-canva already holds Key Vault
    // Secrets User; that grant is inert without both of these. Same
    // pattern as the three governance apps.
    envVars: [
      {
        name: 'KEY_VAULT_URI'
        value: keyVaultUri
      }
      {
        name: 'AZURE_CLIENT_ID'
        value: idMcpCanva.outputs.clientId
      }
    ]
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
    image: mcpSmokeContainerImage
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

// ---------------------------------------------------------------------
// S8 LOOP E2E SMOKE JOB (session/s8-first-loop, plan step 20) — begin
// (append-only: every line above this point was unchanged by session/s8
// except the one named MCP_WEB_ALLOWLIST value-only carve-out authorized
// by DE-6/AC-17. Later additions above this line, recorded here so this
// note stays true rather than quietly stale: the mcpWebLiveMode param and
// ca-mcp-web's MCP_WEB_LIVE_MODE env var -- see that param's own comment
// for why an undeclared, hand-set flag needed to become template state.)
// ---------------------------------------------------------------------

module orchestratorLoopE2eSmokeJob 'modules/orchestrator/loop-e2e-smoke-job.bicep' = {
  name: 'orchestrator-loop-e2e-smoke-job'
  params: {
    location: location
    environmentId: containerAppsEnvironment.outputs.environmentId
    acrLoginServer: containerRegistry.outputs.loginServer
    userAssignedIdentityId: orchestratorIdentity.outputs.identityId
    orchestratorImage: orchestratorContainerImage
    orchestratorRunsUrl: 'https://${orchestratorContainerApp.outputs.internalFqdn}/runs'
    serviceBusNamespaceName: serviceBus.outputs.namespaceName
  }
  dependsOn: [
    orchestratorContainerApp
    orchestratorMigrationJob
  ]
}

output orchestratorLoopE2eSmokeJobName string = orchestratorLoopE2eSmokeJob.outputs.jobName

// ---------------------------------------------------------------------
// S8 LOOP E2E SMOKE JOB — end
// ---------------------------------------------------------------------

// ---------------------------------------------------------------------
// session/s9-analytics: begin (append-only additions — every line above
// this point is byte-identical to origin/main)
//
// analytics-ingest: nightly Buffer/GA4/Search Console/LinkedIn ingestion,
// UTM reconciliation, KPI rollups, and a Fabric shortcut export, plus a
// Vault-utilisation KPI sourced from Vault's own GET /utilisation/rollup.
// See infra/modules/analytics/main.bicep for the 5 child modules this
// orchestrates (managed identity, blob container, migration job, the
// Schedule-triggered nightly ingestion job, and the gated one-shot Buffer
// introspection smoke job).
//
// Reuses the SAME containerRegistry/containerAppsEnvironment/postgres/
// keyVault/storage module instances every other session's block above
// already declares — this block only ever reads their .outputs.*, never
// redeclares a second resource (same convention session/s2-vault,
// session/s3-orchestrator, and the MCP block above all follow).
// ---------------------------------------------------------------------

var analyticsMigrationSql = loadTextContent('../services/analytics-ingest/migrations/0001_analytics_init.sql')

@description('analytics-ingest container image reference for caj-analytics-nightly-ingest. Defaults to a public MCR placeholder needing no registry auth at all (L-0060/L-0061). Unlike some other services\' image params in this file, deploy-infra.yml has NO preserve-current-image preflight for this param — every deploy-infra run resets this back to the MCR placeholder. .github/workflows/analytics-image.yml\'s workflow_run-chained deploy job (triggered on deploy-infra completion, via `az containerapp job update --image`) is what re-applies the real, SHA-pinned image afterward.')
param analyticsNightlyIngestContainerImage string = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

@description('analytics-ingest container image reference for caj-analytics-buffer-smoke. Same bootstrap pattern as analyticsNightlyIngestContainerImage above.')
param analyticsBufferSmokeContainerImage string = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

// Declared for interface consistency with the governanceDeployToken/
// vaultDeployToken/orchestratorDeployToken/mcpDeployToken pattern used by
// every prior session's block above — NOT currently consumed by
// infra/modules/analytics/main.bicep's two Container Apps Jobs, since
// Microsoft.App/jobs has no revisionSuffix/activeRevisionsMode concept at
// all (unlike a Microsoft.App/containerApps resource, a Job re-runs its
// persisted template fresh on every `job start`, so the governance-round-4
// "force a fresh revision so a rotated secret value is picked up" problem
// this pattern solves for Container Apps does not apply here). Kept as a
// no-op parameter so a future Container App added to this module can adopt
// the same pattern without a touch-scope-breaking param addition later.
@description('Deployment-time token, same governance-round-4 pattern as governanceDeployToken/vaultDeployToken/orchestratorDeployToken/mcpDeployToken above. Not currently consumed (see comment above) — Microsoft.App/jobs has no revision concept. Defaults to utcNow(), evaluated once per `az deployment group create`/`what-if` run.')
param analyticsDeployToken string = utcNow()

module analytics 'modules/analytics/main.bicep' = {
  name: 'analytics'
  params: {
    location: location
    // environmentId, postgresFqdn, keyVaultName/Id, storageAccountName/Id,
    // and acrRegistryName/Id below already make this depend on
    // containerAppsEnvironment, postgres, keyVault, storage, and
    // containerRegistry — no explicit dependsOn needed (see this file's
    // DEPENDSON POLICY comment near the top).
    environmentId: containerAppsEnvironment.outputs.environmentId
    postgresFqdn: postgres.outputs.fqdn
    administratorLogin: administratorLogin
    administratorLoginPassword: administratorLoginPassword
    migrationSql: analyticsMigrationSql
    keyVaultName: keyVault.outputs.vaultName
    keyVaultId: keyVault.outputs.vaultId
    storageAccountName: storage.outputs.storageAccountName
    storageAccountId: storage.outputs.storageAccountId
    acrRegistryName: containerRegistry.outputs.registryName
    acrRegistryId: containerRegistry.outputs.registryId
    vaultApiBaseUrl: 'https://${vault.outputs.containerAppInternalFqdn}'
    nightlyIngestContainerImage: analyticsNightlyIngestContainerImage
    bufferSmokeContainerImage: analyticsBufferSmokeContainerImage
  }
}

output analyticsManagedIdentityId string = analytics.outputs.managedIdentityId
output analyticsMigrationJobName string = analytics.outputs.migrationJobName
output analyticsNightlyIngestJobName string = analytics.outputs.nightlyIngestJobName
output analyticsBufferSmokeJobName string = analytics.outputs.bufferSmokeJobName
output analyticsBlobContainerName string = analytics.outputs.blobContainerName

// ---------------------------------------------------------------------
// session/s9-analytics: end
// ---------------------------------------------------------------------
