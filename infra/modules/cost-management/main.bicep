// Canvas Marketing OS — infra/modules/cost-management/main.bicep
//
// ZAR 3000/month cmos-dev infra budget cap: alerts at 50%/80% (Teams),
// automated shutoff at 100% (stop Postgres + scale every Container App to
// 0 replicas), plus a shutoff-executed Teams alert. Composes 4 child
// modules:
//   alert-notify-workflow.bicep     — la-cost-alert-notify (Logic App)
//   emergency-shutoff-workflow.bicep — la-cost-emergency-shutoff (Logic App)
//   budget.bicep                     — the budget + 3 Action Groups
//   shutoff-rbac.bicep               — Contributor grants, scoped per
//                                       resource, for the shutoff workflow
//
// ============================================================================
// CAVEATS THIS SESSION COULD NOT VERIFY LIVE — read before relying on this.
// No Azure CLI/portal access was reachable from this session (network
// egress to management.azure.com is blocked by this environment's policy;
// see the session's own history for the diagnosis). Everything below
// compiles against the documented ARM/Bicep schemas this session COULD
// verify (via Microsoft Learn search), but none of it has run against a
// real deployment.
//
//   1. CURRENCY — see budget.bicep's header. `amount: 3000` assumes the
//      subscription bills in ZAR. Verify before/immediately after deploy.
//   2. LAG — Azure Cost Management's cost data lags actual spend by up to
//      ~24h. This is a backstop against sustained overspend, not a
//      real-time hard ceiling.
//   3. CONTAINER APPS PATCH SEMANTICS — emergency-shutoff-workflow.bicep's
//      scale-to-zero action assumes ARM's PATCH verb merges
//      `properties.template.scale` into the existing resource rather than
//      replacing `properties.template` wholesale. This is consistent with
//      documented ARM PATCH (merge-patch) behavior but not exercised here.
//   4. END-TO-END CHAIN — budget threshold -> Action Group -> Logic App
//      Request trigger -> ManagedServiceIdentity-authenticated HTTP calls
//      to management.azure.com is each individually a documented, standard
//      pattern (verified against Microsoft Learn during this session), but
//      the full chain has never fired for real. STRONGLY RECOMMENDED:
//      before trusting the automatic 100% trigger, a human should manually
//      POST to la-cost-emergency-shutoff's trigger URL once (Azure Portal
//      -> Logic App -> Overview -> Trigger History, or `az rest`) during a
//      planned, low-stakes window, confirm Postgres actually stops and
//      Container Apps actually scale to 0, then manually restart
//      everything before relying on the 100% path unattended. The same
//      applies to la-cost-alert-notify for the 50%/80% paths, lower stakes
//      but still unverified.
//   5. RESIDUAL FIXED COSTS — stopping Postgres and scaling every Container
//      App to 0 does NOT zero out the subscription's spend. Service Bus
//      (Standard tier), the shared Container Registry (Basic tier), Key
//      Vault, and Storage all keep billing at their own fixed/usage-based
//      rates regardless of whether any compute is running.
// ============================================================================

@description('Azure region.')
param location string = resourceGroup().location

@description('Postgres Flexible Server name (infra/modules/postgres.bicep output).')
param postgresServerName string

@description('Names of every Container App in cae-cmos-dev — the full list the emergency shutoff scales to 0. Update this if a new Container App is ever added to the platform (see infra/main.bicep for the authoritative list of what exists).')
param containerAppNames array

@description('Key Vault name holding the teams-webhook-url secret (infra/modules/key-vault.bicep output).')
param keyVaultName string

@description('Monthly budget amount — see budget.bicep\'s CURRENCY CAVEAT.')
param budgetAmount int = 3000

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

// .getSecret() resolves the ACTUAL SECRET VALUE at deployment time, through
// a channel Azure never persists into deployment history/outputs — the
// deploying principal (deploy-infra.yml's own service principal) needs
// read access to this secret, same as any other Key-Vault-backed value this
// template already threads. This is deliberately NOT the same mechanism
// every OTHER Teams-webhook consumer in this repo uses (a Key Vault URL
// resolved by the CONSUMING Container App's own runtime, e.g.
// gatekeeper-app.bicep's teamsWebhookUrlKeyVaultUrl) — a Logic App has no
// equivalent "resolve this Key Vault reference for me" runtime mechanism,
// so the secret value itself has to reach it as a secure workflow
// parameter instead. Passing the raw value through Bicep's .getSecret()
// keeps it out of deployment history either way.
//
// .getSecret() can ONLY be used directly inline as a module parameter's
// value (Bicep BCP180) — it cannot be assigned to a `var` first, so the
// call is repeated at each of the two call sites below rather than
// factored out.

module alertNotifyWorkflow 'alert-notify-workflow.bicep' = {
  name: 'cost-alert-notify-workflow'
  params: {
    location: location
    teamsWebhookUrl: keyVault.getSecret('teams-webhook-url')
  }
}

module emergencyShutoffWorkflow 'emergency-shutoff-workflow.bicep' = {
  name: 'cost-emergency-shutoff-workflow'
  params: {
    location: location
    postgresServerName: postgresServerName
    containerAppNames: containerAppNames
    teamsWebhookUrl: keyVault.getSecret('teams-webhook-url')
  }
}

module shutoffRbac 'shutoff-rbac.bicep' = {
  name: 'cost-shutoff-rbac'
  params: {
    postgresServerName: postgresServerName
    containerAppNames: containerAppNames
    principalId: emergencyShutoffWorkflow.outputs.principalId
  }
}

module budget 'budget.bicep' = {
  name: 'cost-budget'
  params: {
    budgetAmount: budgetAmount
    alertNotifyWorkflowId: alertNotifyWorkflow.outputs.workflowId
    alertNotifyWorkflowCallbackUrl: alertNotifyWorkflow.outputs.triggerCallbackUrl
    emergencyShutoffWorkflowId: emergencyShutoffWorkflow.outputs.workflowId
    emergencyShutoffWorkflowCallbackUrl: emergencyShutoffWorkflow.outputs.triggerCallbackUrl
  }
}

output alertNotifyWorkflowName string = alertNotifyWorkflow.outputs.workflowName
output emergencyShutoffWorkflowName string = emergencyShutoffWorkflow.outputs.workflowName
output budgetName string = budget.outputs.budgetName
