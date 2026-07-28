// Canvas Marketing OS — infra/modules/scheduling/daily-signal-loop-trigger.bicep
//
// One of exactly 3 Logic App scheduling triggers (AC-023) — daily 06:00
// SAST signal loop. SystemAssigned managed identity; sends a
// heartbeat-event-shaped JSON body onto the Service Bus `event` queue via
// an HTTP action authenticated with ManagedServiceIdentity against the
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

resource dailySignalLoopTrigger 'Microsoft.Logic/workflows@2019-05-01' = {
  name: 'la-daily-signal-loop-trigger'
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
            frequency: 'Day'
            interval: 1
            schedule: {
              hours: ['6']
              minutes: [0]
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
              loop_id: 'daily-signal-loop'
              fired_at: '@{utcNow()}'
              source: 'logic-app:dailySignalLoopTrigger'
            }
          }
        }
      }
    }
  }
}

// AC-024: Azure Service Bus Data Sender — GUID resolved live via
// services/orchestrator/scripts/verify_rbac_guids.py (F2). Logic Apps
// only publish heartbeats, so only Sender is granted (never Receiver).
resource dailySignalLoopTriggerServiceBusDataSender 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(serviceBusNamespace.id, dailySignalLoopTrigger.name, 'Azure Service Bus Data Sender')
  scope: serviceBusNamespace
  properties: {
    principalId: dailySignalLoopTrigger.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '69a216fc-b8fb-44d8-bc22-1f3c2cd27a39'
    )
  }
}

output workflowName string = dailySignalLoopTrigger.name
output principalId string = dailySignalLoopTrigger.identity.principalId
