// Canvas Marketing OS — infra/modules/scheduling/source-discovery-trigger.bicep
//
// Weekly heartbeat for source-discovery-loop: probe candidate sources in
// the sandbox, score them, raise ONE approval card. Monday 05:00 SAST, an
// hour before the daily scan, so a card is waiting when the week starts
// rather than landing mid-morning.
//
// Weekly rather than daily on purpose: candidate sources do not change
// shape hour to hour, and a card a person must read is a cost -- one
// considered card a week beats seven ignored ones.
//
// Structurally identical to daily-signal-loop-trigger.bicep (same
// heartbeat body, same managed identity, same Sender-only RBAC): Logic
// Apps publish heartbeats and nothing else.

@description('Azure region.')
param location string = resourceGroup().location

@description('Existing Service Bus namespace name (infra/modules/service-bus.bicep output).')
param serviceBusNamespaceName string

@description('Windows time zone name Logic Apps recurrence requires (not IANA).')
param scheduleTimeZone string = 'South Africa Standard Time'

resource serviceBusNamespace 'Microsoft.ServiceBus/namespaces@2022-10-01-preview' existing = {
  name: serviceBusNamespaceName
}

resource sourceDiscoveryTrigger 'Microsoft.Logic/workflows@2019-05-01' = {
  name: 'la-source-discovery-trigger'
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
            frequency: 'Week'
            interval: 1
            schedule: {
              weekDays: ['Monday']
              hours: ['5']
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
              loop_id: 'source-discovery-loop'
              fired_at: '@{utcNow()}'
              source: 'logic-app:sourceDiscoveryTrigger'
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
resource sourceDiscoveryTriggerServiceBusDataSender 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(serviceBusNamespace.id, sourceDiscoveryTrigger.name, 'Azure Service Bus Data Sender')
  scope: serviceBusNamespace
  properties: {
    principalId: sourceDiscoveryTrigger.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '69a216fc-b8fb-44d8-bc22-1f3c2cd27a39'
    )
  }
}

output workflowName string = sourceDiscoveryTrigger.name
output principalId string = sourceDiscoveryTrigger.identity.principalId
