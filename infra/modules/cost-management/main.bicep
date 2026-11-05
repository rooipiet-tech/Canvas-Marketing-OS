// Canvas Marketing OS — infra/modules/cost-management/main.bicep
//
// USD $200/month cmos-dev infra budget cap: alerts at 50%/80% (Teams),
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
//   1. CURRENCY — see budget.bicep's header. `amount: 200` is USD (the
//      subscription's confirmed billing currency, per the budget owner —
//      not independently verified live from this session).
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

@description('Monthly budget amount in USD — see budget.bicep\'s CURRENCY note.')
param budgetAmount int = 200

// The Teams webhook URL both cost Logic Apps post to, passed in by the deploy
// workflow from the TEAMS_WEBHOOK_URL GitHub secret (the same value as the
// teams-webhook-url Key Vault secret the gatekeeper and orchestrator read).
//
// It is NOT read from Key Vault at deployment time with .getSecret(). That
// made ARM itself read the vault, which this repo's vault refuses by design
// (publicNetworkAccess Disabled, default-deny firewall, no
// enabledForTemplateDeployment), and the first real deploy failed with
// KeyVaultParameterReferenceSecretRetrieveFailed: "Access denied to first
// party service". A Logic App has no runtime "resolve this Key Vault
// reference for me" mechanism, so the value has to reach it as a secure
// workflow parameter; passing it as a secure Bicep parameter keeps it out of
// deployment history and outputs exactly as .getSecret() did, without opening
// the vault to ARM. @minLength(1) so a deploy with the secret unset fails at
// the parameter instead of publishing a cost alert that posts nowhere.
@secure()
@minLength(1)
@description('Teams webhook URL for the cost alerts and emergency shutoff notifications. Supplied by the deploy workflows from the TEAMS_WEBHOOK_URL GitHub secret.')
param teamsWebhookUrl string

module alertNotifyWorkflow 'alert-notify-workflow.bicep' = {
  name: 'cost-alert-notify-workflow'
  params: {
    location: location
    teamsWebhookUrl: teamsWebhookUrl
  }
}

module emergencyShutoffWorkflow 'emergency-shutoff-workflow.bicep' = {
  name: 'cost-emergency-shutoff-workflow'
  params: {
    location: location
    postgresServerName: postgresServerName
    containerAppNames: containerAppNames
    teamsWebhookUrl: teamsWebhookUrl
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
