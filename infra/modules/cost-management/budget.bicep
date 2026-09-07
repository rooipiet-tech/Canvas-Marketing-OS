// Canvas Marketing OS — infra/modules/cost-management/budget.bicep
//
// The ZAR 3000/month cmos-dev infra budget: one Microsoft.Consumption/
// budgets resource, resource-group-scoped, with 3 notification thresholds
// each wired to its own Action Group:
//   50%  -> ag-cost-alert-50   -> la-cost-alert-notify (?threshold=50)
//   80%  -> ag-cost-alert-80   -> la-cost-alert-notify (?threshold=80)
//   100% -> ag-cost-shutoff    -> la-cost-emergency-shutoff (destructive)
//
// CURRENCY CAVEAT — READ BEFORE RELYING ON THIS. `amount` below is a bare
// number; Azure Consumption budgets bill it in the SUBSCRIPTION'S OWN
// billing currency, not an arbitrary currency you pick per-budget. This
// resource sets `amount: 3000` on the assumption the subscription bills in
// ZAR (plausible for a South African subscription/region, but NOT verified
// live from this session — no Azure access here). If the subscription
// actually bills in USD (or any other currency), this budget silently
// becomes 3000 of THAT currency instead of ZAR 3000 — a materially
// different, much larger cap that would defeat the entire point of this
// change. Verify the subscription's billing currency (Cost Management ->
// Overview, or `az account show`/the billing account) before or
// immediately after this deploys, and adjust `amount` if it's not ZAR.
//
// LAG CAVEAT: Azure Cost Management's own cost data lags actual spend by
// up to ~24 hours. These thresholds (and the shutoff they trigger) are a
// backstop against sustained overspend, not a real-time hard ceiling — a
// short, sharp burst can exceed 100% before the shutoff ever fires.

@description('Monthly budget amount, in the subscription\'s own billing currency — see this file\'s CURRENCY CAVEAT above. 3000 assumes ZAR billing.')
param budgetAmount int = 3000

@description('Budget recurrence anchor — must be the first day of a month. Fixed rather than derived from utcNow() so redeploys never try to change an existing budget\'s start date (Azure budgets do not support changing this after creation).')
param budgetStartDate string = '2026-09-01T00:00:00Z'

@description('Far-future end date for the recurring monthly budget (10 years out is the conventional "effectively indefinite" value for this resource type).')
param budgetEndDate string = '2036-09-01T00:00:00Z'

@description('Resource id of la-cost-alert-notify (alert-notify-workflow.bicep output).')
param alertNotifyWorkflowId string

@description('Trigger callback URL of la-cost-alert-notify, WITHOUT any query string — this module appends ?threshold=50/80 itself.')
param alertNotifyWorkflowCallbackUrl string

@description('Resource id of la-cost-emergency-shutoff (emergency-shutoff-workflow.bicep output).')
param emergencyShutoffWorkflowId string

@description('Trigger callback URL of la-cost-emergency-shutoff.')
param emergencyShutoffWorkflowCallbackUrl string

resource alertActionGroup50 'Microsoft.Insights/actionGroups@2023-01-01' = {
  name: 'ag-cost-alert-50'
  location: 'global'
  properties: {
    groupShortName: 'cost50'
    enabled: true
    logicAppReceivers: [
      {
        name: 'alertNotify50'
        resourceId: alertNotifyWorkflowId
        callbackUrl: '${alertNotifyWorkflowCallbackUrl}&threshold=50'
        useCommonAlertSchema: false
      }
    ]
  }
}

resource alertActionGroup80 'Microsoft.Insights/actionGroups@2023-01-01' = {
  name: 'ag-cost-alert-80'
  location: 'global'
  properties: {
    groupShortName: 'cost80'
    enabled: true
    logicAppReceivers: [
      {
        name: 'alertNotify80'
        resourceId: alertNotifyWorkflowId
        callbackUrl: '${alertNotifyWorkflowCallbackUrl}&threshold=80'
        useCommonAlertSchema: false
      }
    ]
  }
}

resource shutoffActionGroup 'Microsoft.Insights/actionGroups@2023-01-01' = {
  name: 'ag-cost-shutoff'
  location: 'global'
  properties: {
    groupShortName: 'costshutof'
    enabled: true
    logicAppReceivers: [
      {
        name: 'emergencyShutoff'
        resourceId: emergencyShutoffWorkflowId
        callbackUrl: emergencyShutoffWorkflowCallbackUrl
        useCommonAlertSchema: false
      }
    ]
  }
}

resource budget 'Microsoft.Consumption/budgets@2023-05-01' = {
  name: 'budget-cmos-dev'
  properties: {
    category: 'Cost'
    amount: budgetAmount
    timeGrain: 'Monthly'
    timePeriod: {
      startDate: budgetStartDate
      endDate: budgetEndDate
    }
    notifications: {
      alert50: {
        enabled: true
        operator: 'GreaterThanOrEqualTo'
        threshold: 50
        thresholdType: 'Actual'
        contactGroups: [
          alertActionGroup50.id
        ]
        contactEmails: []
      }
      alert80: {
        enabled: true
        operator: 'GreaterThanOrEqualTo'
        threshold: 80
        thresholdType: 'Actual'
        contactGroups: [
          alertActionGroup80.id
        ]
        contactEmails: []
      }
      shutoff100: {
        enabled: true
        operator: 'GreaterThanOrEqualTo'
        threshold: 100
        thresholdType: 'Actual'
        contactGroups: [
          shutoffActionGroup.id
        ]
        contactEmails: []
      }
    }
  }
}

output budgetName string = budget.name
