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

@description('Hours with no dispatched task before the loop is considered stalled. 6 covers the daily loop plus a wide margin: the daily-signal-loop fires each morning and takes minutes, so six quiet hours in a working day is already abnormal. Bounded because scheduledQueryRules constrains windowSize to 5m-2d and requires it to be >= evaluationFrequency (PT1H here).')
@minValue(1)
@maxValue(48)
param loopStallHours int = 6

@description('Dead-lettered tasks within the evaluation window before alerting. 1 -- any dead letter is work that was silently dropped, and F6 is precisely that nobody found out.')
param deadLetterThreshold int = 1

@description('QA blocks within the evaluation window before alerting. 5 is deliberately well above normal: a QA block is the Brand Steward gate working correctly, so this fires on an unusual RATE, never on a single legitimate rejection.')
param qaBlockThreshold int = 5

@description('Buffer queue-depth warnings within the evaluation window before alerting. 1 -- the publisher applies the judgement itself, only emitting the event once the queue reaches its warn threshold, so this rule should not second-guess it by waiting for a cluster. NOTE: the emitting side ships in PR #137 (B1); until that merges this rule is inert by construction, not by accident -- see the note above the rule.')
param bufferQueueDepthThreshold int = 1

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
    displayName: 'CMOS: no task dispatched in ${loopStallHours}h'
    description: 'No orchestrator task handler ran to completion within the window. The loop is stalled: this fires on the absence of output, so any cause trips it.'
    severity: 1
    enabled: true
    scopes: [logAnalyticsWorkspaceId]
    evaluationFrequency: 'PT1H'
    // Actually derived from the parameter, which previously reached only
    // displayName -- so setting loopStallHours: 2 produced an alert titled
    // "no task ... in 2h" that still evaluated six hours. A title stating a
    // fact the rule does not check is worse than a hardcoded one.
    windowSize: 'PT${loopStallHours}H'
    criteria: {
      allOf: [
        {
          // `task_dispatched`, not `task_completed`. THE ORCHESTRATOR HAS
          // NEVER EMITTED `task_completed` -- the string appears nowhere in
          // any source file at any point in this repo's history. It came
          // from L-0063's evidence prose, which is itself inaccurate about
          // the events of that day. `db.transition(..., COMPLETED)` is a
          // pure database write with no log line.
          //
          // The real completion-path event is worker.py:301's
          // `task_dispatched`, emitted only AFTER the handler's try/except
          // returns -- every failure path returns early into
          // _retry_or_dead_letter -- so it means the handler ran and came
          // back, which is exactly the "did work come out the other end"
          // question this rule asks.
          //
          // The absence-alert shape is what made the dead term fatal rather
          // than merely silent: `summarize count()` with no `by` returns one
          // row of 0 on empty input, so `LessThanOrEqual 0` was satisfied on
          // the first evaluation and every hour after -- Sev 1, autoMitigate
          // flapping hourly. The rule this PR calls the one that would have
          // caught the 10 Aug-2 Sep outage would instead have been the
          // "fires every morning, gets muted, then ignored" alert this
          // module's own header warns against.
          query: '''
ContainerAppConsoleLogs_CL
| where Log_s has "task_dispatched"
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
          // `cascade_dead_letter` is a FUNCTION name (state_machine.py:116),
          // not an emitted event, and KQL `has` is term-based: the needle
          // terms [cascade, dead, letter] never line up against
          // `task_cascade_dead_lettered` ([task, cascade, dead, lettered] --
          // "letter" is not "lettered"). The one thing it could match is
          // `cascade_dead_letter_noop_already_dead_lettered`, a deliberate
          // no-op, which would have paged at Sev 2.
          //
          // The first disjunct was never blind -- emit_alert puts a
          // DeadLetterAlert on the event queue and worker.py:435 logs
          // `dead_letter_alert_received` -- but that made the second
          // disjunct dead weight that LOOKED like a direct-log fallback for
          // exactly the case where the queue consumer is what broke, which
          // is F6, the failure this rule cites in its own header. And
          // `task_dead_lettered`, the primary non-cascade line, was matched
          // by neither.
          //
          // All four real event names, verified against their emitters:
          //   worker.py:435        dead_letter_alert_received
          //   state_machine.py:105 task_dead_lettered
          //   worker.py:214        task_cascade_dead_lettering
          //   state_machine.py:174 task_cascade_dead_lettered
          query: '''
ContainerAppConsoleLogs_CL
| where Log_s has_any ("dead_letter_alert_received", "task_dead_lettered", "task_cascade_dead_lettered", "task_cascade_dead_lettering")
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
          // Neither `budget_hard_breach` nor `budget_exceeded` is emitted
          // anywhere -- each appeared exactly once in this repo, on this
          // line. model-gateway logs ONE JSON line per request
          // (completion.py:179) with a fixed `"event": "completion"` and the
          // state in a separate `"budget_state"` field; the hard-breach path
          // sets budget_state="hard_breach" (completion.py:440, guarded at
          // :316). `BUDGET_EXHAUSTED` goes only into the HTTP response body,
          // never into a log line.
          //
          // Term-based `has` could not have bridged that: the emitted line
          // tokenises to [..., budget, state, hard, breach, ...], so
          // `has "budget_hard_breach"` needs budget->hard->breach adjacent
          // and "state" intervenes. The rule deployed cleanly and was
          // permanently silent -- worse than absent, because the module then
          // looks like it covers budget breaches.
          //
          // Parsed rather than term-matched, so this is exact: `has` is a
          // cheap prefilter, the parse is the actual test. soft_breach
          // cannot false-positive through it.
          query: '''
ContainerAppConsoleLogs_CL
| where Log_s has "hard_breach"
| where tostring(parse_json(Log_s).budget_state) == "hard_breach"
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

// ---------------------------------------------------------------------
// 5. Buffer's publish queue is not draining (Sev 3) — B1
// ---------------------------------------------------------------------
// Added 2 Sep 2026 as the outcome of backlog item B1, which asked whether
// to buy out Buffer's free-tier queue cap of 10. The answer was no, and
// this rule is what was chosen instead of a subscription.
//
// The arithmetic behind that decision (recorded in full beside
// BUFFER_FREE_TIER_QUEUE_CAP in services/publisher/app/config.py):
// weekly-planning-trigger.bicep fires weekly-content-loop at
// `frequency: 'Day', interval: 1`, and its Monday..Friday task prefixes
// are dependency-chain names rather than a schedule -- so one heartbeat
// runs a COMPLETE content cycle, and a cycle can queue up to four posts
// to one channel against a cap of ten. A stalled queue therefore reaches
// the cap in under three days.
//
// (An earlier revision of this comment said four per WEEK, read off the
// loop file's stale header. Corrected on the same day, along with the
// threshold it justified -- the trigger, not the loop yaml, is the
// authoritative source for cadence.)
//
// THE EMITTER IS NOT ON THIS BRANCH. `buffer_queue_depth_high` and
// BUFFER_QUEUE_DEPTH_WARN_AT ship in PR #137 (B1); on main today the
// publisher does a single binary check AT the cap and records a
// publish_attempts row, with no console log at all. So until #137
// merges this rule matches nothing.
//
// That is deliberate and it is the lesser of two bad options: this
// module does not exist on main either, so putting the rule on B1's
// branch would mean two conflicting copies of the file. It is called out
// here rather than left implicit, because a permanently-silent rule that
// LOOKS like coverage is worse than an absent one -- if #137 is
// abandoned, delete this rule rather than leaving it standing.
//
// Once #137 lands: the publisher emits `buffer_queue_depth_high` from
// BUFFER_QUEUE_DEPTH_WARN_AT (6) upward, one full cycle of headroom below
// the cap. That is the whole point -- by the time posts are being refused
// with buffer_queue_cap_exceeded, a cycle of scheduled content has
// already been lost. This rule fires on the warning, not on the
// refusal.
//
// Sev 3 rather than 2. Nothing has failed yet when this fires -- that is
// the design -- and the response is to look at why the queue is not
// draining, not to be woken up. It becomes Sev-2-shaped only if ignored,
// at which point the refusals speak for themselves in publish_attempts.
//
// ca-publisher shares the cae-cmos-dev environment with the orchestrator,
// so its console logs land in the same workspace and need no new source.
resource bufferQueueDepthAlert 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = {
  name: 'alert-cmos-buffer-queue-depth'
  location: location
  properties: {
    displayName: 'CMOS: Buffer queue is not draining'
    description: 'The publisher saw the Buffer queue at or above its warning depth. B1 kept the free tier and took this signal instead of a paid plan: the queue is stalling with roughly one content cycle of headroom left before posts start being refused.'
    severity: 3
    enabled: true
    scopes: [logAnalyticsWorkspaceId]
    evaluationFrequency: 'PT1H'
    windowSize: 'P1D'
    criteria: {
      allOf: [
        {
          query: '''
ContainerAppConsoleLogs_CL
| where Log_s has "buffer_queue_depth_high"
| summarize queueWarnings = count()
'''
          timeAggregation: 'Total'
          metricMeasureColumn: 'queueWarnings'
          operator: 'GreaterThanOrEqual'
          threshold: bufferQueueDepthThreshold
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
  bufferQueueDepthAlert.name
]
