// Canvas Marketing OS — infra/modules/scheduling/weekly-planning-trigger.bicep
//
// One of exactly 3 Logic App scheduling triggers (AC-023) — originally
// Monday 07:00 SAST weekly planning loop. TEMPORARILY switched to a daily
// 07:00 SAST recurrence on 6 Aug 2026 per Pieter's explicit instruction
// ("change weekly to daily for now so I can approve/reject and we can
// iterate") — intent is a fresh weekly-content-loop cycle landing every
// morning for review, reverting to weekly once the iteration phase is
// done. SystemAssigned managed identity; sends a heartbeat-event-shaped
// JSON body onto the Service Bus `event` queue via an HTTP action
// authenticated with ManagedServiceIdentity against the
// https://servicebus.azure.net/ audience (C6) — no connection string /
// SAS key anywhere in this module.

@description('Azure region.')
param location string = resourceGroup().location

@description('Existing Service Bus namespace name (infra/modules/service-bus.bicep output).')
param serviceBusNamespaceName string

@description('Windows time zone name Logic Apps recurrence requires (not IANA).')
param scheduleTimeZone string = 'South Africa Standard Time'

resource serviceBusNamespace 'Microsoft.ServiceBus/namespaces@2022-10-01-preview' existing = {
  name: serviceBusNamespaceName
}

resource weeklyPlanningTrigger 'Microsoft.Logic/workflows@2019-05-01' = {
  name: 'la-weekly-planning-trigger'
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    state: 'Enabled'
    definition: {
      '$schema': 'https://schema.management.azure.com/providers/Microsoft.Logic/schemas/2016-06-01/workflowdefinition.json#'
      contentVersion: '1.0.0.0'
      triggers: {
        Recurrence: {
          type: 'Recurrence'
          recurrence: {
            // TEMPORARY (6 Aug 2026): frequency Day/interval 1 instead of
            // Week/weekDays:['Monday'] — see header note. Revert to the
            // weekly shape once the daily-review iteration is done:
            //   frequency: 'Week'
            //   interval: 1
            //   schedule: { weekDays: ['Monday'], hours: ['7'], minutes: [0] }
            frequency: 'Day'
            interval: 1
            schedule: {
              hours: ['7']
              minutes: [0]
            }
          }
          timeZone: scheduleTimeZone
        }
      }
    }
    actions: {
      SendHeartbeatToServiceBus: {
        type: 'Http'
        inputs: {
          method: 'POST'
          uri: 'https://${serviceBusNamespaceName}.servicebus.windows.net/event/messages'
          authentication: {
            type: 'ManagedServiceIdentity'
            audience: 'https://servicebus.azure.net/'
          }
          headers: {
            'Content-Type': 'application/json'
          }
          body: {
            envelope_version: '1'
            event_type: 'heartbeat'
            event_id: '@{guid()}'
            loop_id: 'weekly-content-loop'
            fired_at: '@{utcNow()}'
            source: 'logic-app:weeklyPlanningTrigger'
          }
        }
      }
    }
  }
}

// AC-024: Azure Service Bus Data Sender — GUID resolved live via
// services/orchestrator/scripts/verify_rbac_guids.py (F2). Logic Apps
// only publish heartbeats, so only Sender is granted (never Receiver).
resource weeklyPlanningTriggerServiceBusDataSender 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(serviceBusNamespace.id, weeklyPlanningTrigger.name, 'Azure Service Bus Data Sender')
  scope: serviceBusNamespace
  properties: {
    principalId: weeklyPlanningTrigger.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '69a216fc-b8fb-44d8-bc22-1f3c2cd27a39'
    )
  }
}

output workflowName string = weeklyPlanningTrigger.name
output principalId string = weeklyPlanningTrigger.identity.principalId
