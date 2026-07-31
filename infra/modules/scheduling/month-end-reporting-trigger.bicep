// Canvas Marketing OS — infra/modules/scheduling/month-end-reporting-trigger.bicep
//
// One of exactly 3 Logic App scheduling triggers (AC-023) — last-day-of-
// month reporting loop. SystemAssigned managed identity; sends a
// heartbeat-event-shaped JSON body onto the Service Bus `event` queue via
// an HTTP action authenticated with ManagedServiceIdentity against the
// https://servicebus.azure.net/ audience (C6) — no connection string /
// SAS key anywhere in this module. This trigger is infra-only — AC-005
// only mandates the 2 shipped loop YAML files (daily-signal-loop,
// weekly-content-loop); a heartbeat with an unrecognized loop_id
// ("month-end-reporting") is logged and skipped by
// worker.handle_heartbeat_message (never crashes the worker loop) until a
// matching loop file ships in a future session.

@description('Azure region.')
param location string = resourceGroup().location

@description('Existing Service Bus namespace name (infra/modules/service-bus.bicep output).')
param serviceBusNamespaceName string

@description('Windows time zone name Logic Apps recurrence requires (not IANA).')
param scheduleTimeZone string = 'South Africa Standard Time'

resource serviceBusNamespace 'Microsoft.ServiceBus/namespaces@2022-10-01-preview' existing = {
  name: serviceBusNamespaceName
}

resource monthEndReportingTrigger 'Microsoft.Logic/workflows@2019-05-01' = {
  name: 'la-month-end-reporting-trigger'
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
            frequency: 'Month'
            interval: 1
            schedule: {
              monthDays: [-1]
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
              loop_id: 'month-end-reporting'
              fired_at: '@{utcNow()}'
              source: 'logic-app:monthEndReportingTrigger'
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
resource monthEndReportingTriggerServiceBusDataSender 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(serviceBusNamespace.id, monthEndReportingTrigger.name, 'Azure Service Bus Data Sender')
  scope: serviceBusNamespace
  properties: {
    principalId: monthEndReportingTrigger.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '69a216fc-b8fb-44d8-bc22-1f3c2cd27a39'
    )
  }
}

output workflowName string = monthEndReportingTrigger.name
output principalId string = monthEndReportingTrigger.identity.principalId
