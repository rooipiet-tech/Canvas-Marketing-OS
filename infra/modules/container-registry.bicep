// Canvas Marketing OS — container-registry.bicep
//
// THIS IS THE SINGLE CANONICAL SHARED REGISTRY for the whole platform — a
// shared, canonical ACR, not a gateway-private resource. Any other service
// that needs to push or pull images (session s2-vault is the first known
// future consumer) MUST consume this module and pass its own service
// principal id via pullPrincipalId, rather than authoring a second
// Microsoft.ContainerRegistry resource anywhere in infra/.
//
// REBASE RULE: on a merge/rebase conflict against main in this file,
// main's version wins. Re-apply your consumer wiring on top of it instead of
// keeping your branch's copy.
//
// The registry name is passed in (computed deterministically once, in
// main.bicep) rather than derived here, so exactly one string is the source
// of truth. It is deterministic — never a session-random suffix — so
// repeated deployments are idempotent.
//
// NO DEPENDENCY ON ANY CONSUMER (fix/deploy-infra-gateway): this module
// takes no input from gateway.bicep or any other consumer, on purpose. An
// earlier revision had this module's pullPrincipalId wired to the gateway
// app's principalId, which — combined with gateway.bicep reading this
// module's loginServer back — would have made the two modules depend on
// each other in both directions, a genuine cycle Bicep refuses to compile.
// The earlier (broken) fix was to give gateway.bicep a separately-computed
// name string instead of this module's real output, which removed the
// cycle but also removed the ordering guarantee — ARM had no reason to
// provision this registry before the gateway Container App that pulls from
// it, and on a fresh environment it didn't, producing a
// "failed to resolve registry ... no such host" deployment failure.
// gateway.bicep now breaks the cycle the other way: it reads this module's
// REAL outputs (a genuine, correct one-directional dependency) and grants
// its own identity AcrPull by pulling in this registry as an `existing`
// resource internally, rather than asking this module to do it. This
// module's pullPrincipalId parameter remains for any OTHER future consumer
// that doesn't have this dependency problem — it is simply not the
// mechanism gateway.bicep uses.
//
// KNOWN-HARD RISK — provider registration lag (L-0007 class): deploy-infra.yml's
// preflight registers/verifies Microsoft.App, Microsoft.DBforPostgreSQL and
// Microsoft.ServiceBus only, and that workflow is outside this build's locked
// touch-scope, so Microsoft.ContainerRegistry is never checked. If this
// subscription has never used ACR before, the first deployment referencing
// this module may fail with a MissingSubscriptionRegistration-style error.
// Whoever runs the first real deploy should be ready to run
//   az provider register --namespace Microsoft.ContainerRegistry
// and retry. Documentation-only mitigation: no code fix is possible without
// editing deploy-infra.yml. Also recorded in docs/accepted-risks.md.
//
// Cost/posture: Basic SKU with the admin account disabled — the cheapest
// option that still supports managed-identity image pull. Recorded as an
// accepted risk in docs/accepted-risks.md.

@description('Azure region.')
param location string = resourceGroup().location

@description('Globally-unique registry name, computed deterministically in main.bicep.')
param registryName string

@description('Principal id granted AcrPull on this registry. Empty string skips the role assignment. Deliberately generic so any consuming service can pass its own identity.')
param pullPrincipalId string = ''

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: registryName
  location: location
  sku: {
    name: 'Basic'
  }
  tags: {
    purpose: 'shared platform container registry'
  }
  properties: {
    adminUserEnabled: false
  }
}

// AcrPull (7f951dda-4ed3-4680-a7ca-43fe172d538d) — the only pull path, since
// the admin account is disabled and no static registry credential exists.
resource acrPullRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(pullPrincipalId)) {
  name: guid(registry.id, pullPrincipalId, 'AcrPull')
  scope: registry
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
    principalId: pullPrincipalId
    principalType: 'ServicePrincipal'
  }
}

output loginServer string = registry.properties.loginServer
output registryName string = registry.name
output registryId string = registry.id
