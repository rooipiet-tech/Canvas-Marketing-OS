// Canvas Marketing OS — infra/modules/monitoring/alerts.bicep
//
// A2 (F6, O2, B5). Before this module, `infra/` contained NO
// metricAlerts, NO scheduledQueryRules and NO action groups. Nothing
// paged on anything. Three specific holes compounded:
//
//   F6  A dead-lettered task emits a DeadLetterAlert onto the `event`
//       queue. worker.py logs `dead_letter_alert_received` and then
//       explicitly does nothing with it -- no consumer, no alert. A task
//       could exhaust its retries and vanish in silence.
//   B5  The orchestrator's worker is a single asyncio.Task inside the
//       FastAPI process. If its startup raised, /health still returned
//       200 and the system stalled while looking healthy. The
//       application half of that is GET /readiness (services/orchestrator/
//       main.py); this module is what actually notices.
//   O2  Any alerting configured portal-side is invisible to everyone
//       reading the repository, which the architecture map treats as a
//       finding in its own right. Alerts that exist only in a portal are
//       alerts nobody can review, diff, or restore.
//
// WHY LOG QUERIES RATHER THAN METRIC ALERTS. Every condition below is a
// statement about the orchestrator's own structured JSON log events --
// `dead_letter_alert_received`, the QA_BLOCKED transition, the budget
// guard's refusal, task completions. None of them is an Azure platform
// metric, so none can be expressed as a metricAlert. They are
// scheduledQueryRules over the SAME log-cmos-dev workspace the Container
// Apps environment already streams to (container-apps-environment.bicep
// creates it and this module is passed its id) -- no new data pipeline,
// no new agent, no new cost beyond the queries themselves.
//
// SEVERITY, deliberately not uniform:
//   Sev 1 -- the loop has stopped producing. Nothing else being wrong
//            matters if this fires.
//   Sev 2 -- work is being lost (dead letters) or spend has breached a
//            hard ceiling.
//   Sev 3 -- QA is blocking unusually often. Worth a look, not a
//            wake-up: a QA block is the gate WORKING, and the alert is
//            for an unusual RATE of them, not for their existence.
//
// THE THRESHOLDS ARE FIRST GUESSES AND ARE MARKED AS SUCH. Nobody has a
// baseline for any of these numbers, because nothing has ever measured
// them. They are set where a human would want to know, not where a
// statistician would put them, and each carries the reasoning so the
// next person tunes it deliberately rather than rediscovering it. An
// alert that fires every morning gets muted and then ignored, which is
// worse than no alert -- so if one of these turns out noisy, change the
// number here rather than disabling the rule.

@description('Azure region for the alert rules.')
param location string = resourceGroup().location

@description('Resource id of the existing log-cmos-dev Log Analytics workspace (container-apps-environment.bicep output) — no new workspace is created here.')
param logAnalyticsWorkspaceId string

@description('Email address the action group notifies. Empty disables the email receiver, leaving rules that evaluate and record without paging anyone — which is the honest default until an owner address is agreed rather than guessed.')
param alertEmailAddress string = ''

@description('Hours with no completed task before the loop is considered stalled. 6 covers the daily loop plus a wide margin: the daily-signal-loop fires each morning and takes minutes, so six quiet hours in a working day is already abnormal.')
param loopStallHours int = 6

@description('Dead-lettered tasks within the evaluation window before alerting. 1 -- any dead letter is work that was silently dropped, and F6 is precisely that nobody found out.')
param deadLetterThreshold int = 1

@description('QA blocks within the evaluation window before alerting. 5 is deliberately well above normal: a QA block is the Brand Steward gate working correctly, so this fires on an unusual RATE, never on a single legitimate rejection.')
param qaBlockThreshold int = 5

var hasEmail = !empty(alertEmailAddress)

resource actionGroup 'Microsoft.Insights/actionGroups@2023-01-01' = {
  name: 'ag-cmos-dev'
  location: 'global'
  properties: {
    groupShortName: 'cmosdev'
    enabled: true
    emailReceivers: hasEmail ? [
      {
        name: 'owner'
        emailAddress: alertEmailAddress
        useCommonAlertSchema: true
      }
    ] : []
  }
}

// A rule with no receivers still evaluates and still records its own
// firing history, so the rules below are wired to the group
// unconditionally. That way turning on paging later is a parameter
// change, not an infrastructure change, and the firing history that
// accumulates in the meantime is exactly the baseline the thresholds
// above are currently missing.
var actionGroupIds = [actionGroup.id]

// ---------------------------------------------------------------------
// 1. The loop has stopped producing (Sev 1)
// ---------------------------------------------------------------------
// The one alert that would have caught the 10 Aug - 2 Sep outage from
// the outside. Throughout it, every deploy was green, /health was 200,
// and no task completed -- because the scan dead-lettered daily and
// cascaded. This asks the only question that matters: is work coming out
// the other end?
//
// Inverted by design: it fires on the ABSENCE of a signal, so it cannot
// be defeated by the failure mode being novel. Anything that stops the
// loop trips it, including causes nobody has thought of yet.
resource loopStalledAlert 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = {
  name: 'alert-cmos-loop-stalled'
  location: location
  properties: {
    displayName: 'CMOS: no task completed in ${loopStallHours}h'
    description: 'No orchestrator task reached a completed state within the window. The loop is stalled: this fires on the absence of output, so any cause trips it.'
    severity: 1
    enabled: true
    scopes: [logAnalyticsWorkspaceId]
    evaluationFrequency: 'PT1H'
    windowSize: 'PT6H'
    criteria: {
      allOf: [
        {
          query: '''
ContainerAppConsoleLogs_CL
| where Log_s has "task_completed"
| summarize completions = count()
'''
          timeAggregation: 'Total'
          metricMeasureColumn: 'completions'
          operator: 'LessThanOrEqual'
          threshold: 0
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    autoMitigate: true
    actions: {
      actionGroups: actionGroupIds
    }
  }
}

// ---------------------------------------------------------------------
// 2. Work is being dropped (Sev 2) — F6
// ---------------------------------------------------------------------
// worker.py logs `dead_letter_alert_received` and does nothing else with
// it. This is the consumer that finding asked for -- not in the worker,
// where a second in-process consumer would share the fate of the first,
// but outside the process entirely.
resource deadLetterAlert 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = {
  name: 'alert-cmos-dead-letter'
  location: location
  properties: {
    displayName: 'CMOS: task dead-lettered'
    description: 'A task exhausted its retries and was dead-lettered. F6: the DeadLetterAlert event has no in-process consumer, so this rule is the one thing that notices.'
    severity: 2
    enabled: true
    scopes: [logAnalyticsWorkspaceId]
    evaluationFrequency: 'PT15M'
    windowSize: 'PT1H'
    criteria: {
      allOf: [
        {
          query: '''
ContainerAppConsoleLogs_CL
| where Log_s has "dead_letter_alert_received" or Log_s has "cascade_dead_letter"
| summarize deadLetters = count()
'''
          timeAggregation: 'Total'
          metricMeasureColumn: 'deadLetters'
          operator: 'GreaterThanOrEqual'
          threshold: deadLetterThreshold
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    autoMitigate: true
    actions: {
      actionGroups: actionGroupIds
    }
  }
}

// ---------------------------------------------------------------------
// 3. Spend breached a hard ceiling (Sev 2)
// ---------------------------------------------------------------------
// The budget guard already refuses over-ceiling work; what it does not
// do is tell anyone it happened. A refusal is safe but it is also
// silently degraded output -- the loop keeps running and produces less.
resource budgetBreachAlert 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = {
  name: 'alert-cmos-budget-breach'
  location: location
  properties: {
    displayName: 'CMOS: hard budget ceiling breached'
    description: 'The budget guard refused work against a hard ceiling. The refusal is safe; the silence about it is not.'
    severity: 2
    enabled: true
    scopes: [logAnalyticsWorkspaceId]
    evaluationFrequency: 'PT1H'
    windowSize: 'PT6H'
    criteria: {
      allOf: [
        {
          query: '''
ContainerAppConsoleLogs_CL
| where Log_s has "budget_hard_breach" or Log_s has "budget_exceeded"
| summarize breaches = count()
'''
          timeAggregation: 'Total'
          metricMeasureColumn: 'breaches'
          operator: 'GreaterThanOrEqual'
          threshold: 1
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    autoMitigate: true
    actions: {
      actionGroups: actionGroupIds
    }
  }
}

// ---------------------------------------------------------------------
// 4. QA is blocking unusually often (Sev 3)
// ---------------------------------------------------------------------
// A QA block is the Brand Steward gate WORKING, and one of them is not a
// problem -- deploy-loop-e2e-smoke treats a legitimate QA_BLOCKED as a
// pass for exactly that reason. A cluster of them is different: it means
// either the writers have drifted or the gate has, and both are worth a
// look. Sev 3 and a deliberately high threshold, because an alert that
// fires on correct behaviour is one people learn to ignore.
resource qaBlockRateAlert 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = {
  name: 'alert-cmos-qa-block-rate'
  location: location
  properties: {
    displayName: 'CMOS: elevated QA block rate'
    description: 'More than the expected number of QA blocks in the window. A single block is the gate working; a cluster means the writers or the gate have drifted.'
    severity: 3
    enabled: true
    scopes: [logAnalyticsWorkspaceId]
    evaluationFrequency: 'PT6H'
    windowSize: 'P1D'
    criteria: {
      allOf: [
        {
          query: '''
ContainerAppConsoleLogs_CL
| where Log_s has "qa_review_blocked"
| summarize qaBlocks = count()
'''
          timeAggregation: 'Total'
          metricMeasureColumn: 'qaBlocks'
          operator: 'GreaterThan'
          threshold: qaBlockThreshold
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    autoMitigate: true
    actions: {
      actionGroups: actionGroupIds
    }
  }
}

output actionGroupId string = actionGroup.id
output alertNames array = [
  loopStalledAlert.name
  deadLetterAlert.name
  budgetBreachAlert.name
  qaBlockRateAlert.name
]
