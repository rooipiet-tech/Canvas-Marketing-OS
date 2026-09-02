// Canvas Marketing OS — infra/modules/scheduling/weekly-planning-trigger.bicep
//
// One of the Logic App scheduling triggers (AC-023; 5 of them as of
// 17 Aug 2026 — see logic-apps.bicep). Fires weekly-content-loop at
// 07:00 SAST EVERY DAY.
//
// CADENCE IS DAILY, DELIBERATELY — not a leftover. It was switched from
// Monday-weekly to daily on 6 Aug 2026 per Pieter's instruction ("change
// weekly to daily for now so I can approve/reject and we can iterate"),
// and confirmed as the intended standing cadence on 17 Aug 2026. Earlier
// revisions of this file, and the architecture pack's own F3 finding,
// described the daily shape as TEMPORARY with a weekly block commented
// out below it awaiting revert; that revert is NOT coming, so both the
// marker and the dead block are gone. One complete content cycle lands
// every morning for review.
//
// The loop it fires is still named weekly-content-loop and its task ids
// still carry Monday..Friday prefixes. Those are DEPENDENCY-CHAIN names,
// not a schedule: one heartbeat decomposes the whole Mon-Fri graph and
// runs it as fast as depends_on allows. Firing daily therefore means a
// full cycle per day, not one weekday's slice per day.
//
// SystemAssigned managed identity; sends a heartbeat-event-shaped
// JSON body onto the Service Bus `event` queue via an HTTP action
// authenticated with ManagedServiceIdentity against the
// https://servicebus.azure.net/ audience (C6) — no connection string /
// SAS key anywhere in this module.
//
// INCIDENT (6 Aug 2026, deploy-infra #100, commit d18f63b, F-SCHEDULING-
// TRIGGER-SCHEMA): the daily-cadence rewrite above placed `timeZone` as a
// sibling of `recurrence` on the Recurrence trigger, and `actions` as a
// sibling of `definition` under the resource's `properties` — both wrong.
// Confirmed via the actual ARM deployment error, not guessed:
// `Could not find member 'timeZone' on object of type 'FlowTemplateTrigger'`
// (properties.definition.triggers.Recurrence.timeZone) — ARM's Workflow
// Definition Language requires `timeZone` to live *inside* the `recurrence`
// object (alongside frequency/interval/schedule), and `actions` to live
// *inside* `definition` (alongside `triggers`), not as a sibling of it at
// the `properties` level — the latter was already being flagged by Bicep's
// own linter as BCP037 ("property 'actions' is not allowed on objects of
// type 'WorkflowProperties'") but never blocked the build since linter
// warnings don't fail `az deployment group create`; the timeZone placement
// is what actually killed the deploy. Both fixed together below to avoid a
// second failed deploy cycle discovering the second bug.

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
            // Daily at 07:00 SAST — the standing cadence (see header).
            // `hours`/`minutes` are the only schedule keys Azure honours on
            // frequency Day; weekDays is Week-only, which is what
            // test_scheduling_trigger_recurrence.py guards.
            frequency: 'Day'
            interval: 1
            schedule: {
              hours: ['7']
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
              loop_id: 'weekly-content-loop'
              fired_at: '@{utcNow()}'
              source: 'logic-app:weeklyPlanningTrigger'
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
