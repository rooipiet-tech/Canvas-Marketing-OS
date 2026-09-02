// Canvas Marketing OS — infra/modules/scheduling/publish-trigger.bicep
//
// Heartbeat for publish-loop: publish every approved-but-unpublished
// asset. Hourly on weekdays, 06:00–18:00 SAST.
//
// WHY THIS EXISTS. Both content loops terminated at the approval request:
// request-approval, schedule-social-buffer and publish-newsletter each
// raise an approval card and complete, and no task in either graph
// depended on any of them. ca-publisher was never called by anything.
// Nothing consumed a human's approval.
//
// WHY HOURLY, where every other trigger here is daily or weekly. The
// others start work; this one finishes it. Its latency is how long an
// approved asset waits between a person clicking Approve and the post
// appearing, so a daily beat would mean an approval given at 09:05
// publishing the following morning. Hourly is a compromise: the sweep is
// a single indexed query and costs nothing when there is nothing to do
// (it opens no Vault, Gatekeeper or Publisher connection on an empty
// sweep), while a person who approves in the morning sees it live within
// the hour.
//
// Weekday daytime only, not 24/7: an approval clicked at 23:00 on a
// Saturday should not put a post on LinkedIn at 23:05 on a Saturday. It
// waits for Monday morning, which is also when anyone would want it.
//
// Structurally identical to source-discovery-trigger.bicep (same
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

resource publishTrigger 'Microsoft.Logic/workflows@2019-05-01' = {
  name: 'la-publish-trigger'
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
            // Logic Apps rejects `schedule.weekDays` on any frequency but
            // 'Week' (InvalidWorkflowTriggerRecurrenceSchedule: "invalid
            // recurrence frequency 'Hour'", live on deploy-infra #138,
            // which failed the WHOLE main deployment). Week/interval 1 with
            // every weekday hour listed is the supported spelling of the
            // same intent -- and the spelling source-discovery-trigger.bicep
            // already uses. Fires Mon-Fri at 06:10 through 18:10 SAST.
            frequency: 'Week'
            interval: 1
            schedule: {
              weekDays: ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']
              hours: ['6', '7', '8', '9', '10', '11', '12', '13', '14', '15', '16', '17', '18']
              minutes: [10]
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
              loop_id: 'publish-loop'
              fired_at: '@{utcNow()}'
              source: 'logic-app:publishTrigger'
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
resource publishTriggerServiceBusDataSender 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(serviceBusNamespace.id, publishTrigger.name, 'Azure Service Bus Data Sender')
  scope: serviceBusNamespace
  properties: {
    principalId: publishTrigger.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '69a216fc-b8fb-44d8-bc22-1f3c2cd27a39'
    )
  }
}

output workflowName string = publishTrigger.name
output principalId string = publishTrigger.identity.principalId
