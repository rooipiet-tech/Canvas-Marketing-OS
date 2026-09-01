// Canvas Marketing OS — infra/modules/scheduling/logic-apps.bicep
//
// Aggregator entry point (the single module infra/main.bicep references)
// composing the 3 individually-defined scheduling triggers in this same
// directory — one Consumption Logic App resource per sibling file,
// each with its own SystemAssigned identity and its own Service Bus Data
// Sender role assignment (AC-023, AC-024):
//   - daily-signal-loop-trigger.bicep   (daily 06:00 SAST)
//   - source-discovery-trigger.bicep    (weekly Mon 05:00 SAST)
//   - weekly-planning-trigger.bicep     (Monday 07:00 SAST)
//   - month-end-reporting-trigger.bicep (last day of month)

@description('Azure region.')
param location string = resourceGroup().location

@description('Existing Service Bus namespace name (infra/modules/service-bus.bicep output).')
param serviceBusNamespaceName string

@description('Windows time zone name Logic Apps recurrence requires (not IANA).')
param scheduleTimeZone string = 'South Africa Standard Time'

module dailySignalLoopTrigger 'daily-signal-loop-trigger.bicep' = {
  name: 'daily-signal-loop-trigger'
  params: {
    location: location
    serviceBusNamespaceName: serviceBusNamespaceName
    scheduleTimeZone: scheduleTimeZone
  }
}

module weeklyPlanningTrigger 'weekly-planning-trigger.bicep' = {
  name: 'weekly-planning-trigger'
  params: {
    location: location
    serviceBusNamespaceName: serviceBusNamespaceName
    scheduleTimeZone: scheduleTimeZone
  }
}

module sourceDiscoveryTrigger 'source-discovery-trigger.bicep' = {
  name: 'source-discovery-trigger'
  params: {
    location: location
    serviceBusNamespaceName: serviceBusNamespaceName
    scheduleTimeZone: scheduleTimeZone
  }
}

module monthEndReportingTrigger 'month-end-reporting-trigger.bicep' = {
  name: 'month-end-reporting-trigger'
  params: {
    location: location
    serviceBusNamespaceName: serviceBusNamespaceName
    scheduleTimeZone: scheduleTimeZone
  }
}

output dailySignalLoopTriggerName string = dailySignalLoopTrigger.outputs.workflowName
output weeklyPlanningTriggerName string = weeklyPlanningTrigger.outputs.workflowName
output sourceDiscoveryTriggerName string = sourceDiscoveryTrigger.outputs.workflowName
output monthEndReportingTriggerName string = monthEndReportingTrigger.outputs.workflowName
