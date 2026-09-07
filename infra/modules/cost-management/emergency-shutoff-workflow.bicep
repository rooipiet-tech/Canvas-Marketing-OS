// Canvas Marketing OS — infra/modules/cost-management/emergency-shutoff-workflow.bicep
//
// la-cost-emergency-shutoff — the automated-shutoff half of the USD
// $200/month budget cap (see budget.bicep, shutoff-rbac.bicep, and this
// directory's main.bicep for the full picture). Triggered by the budget's
// 100%-threshold notification, via an Action Group's `logicAppReceivers`
// entry pointing at this workflow's Request trigger callback URL.
//
// WHAT THIS ACTUALLY DOES, PLAINLY: stops the Postgres Flexible Server and
// scales every Container App in cae-cmos-dev to 0 replicas. This is a FULL
// PLATFORM OUTAGE, not a graceful degrade — every service depends on
// Postgres (directly or through ca-pgbouncer), so the moment it stops,
// everything else fails closed. Restarting afterwards is a MANUAL step
// (`az postgres flexible-server start` + scaling each Container App's
// min/maxReplicas back up, e.g. by re-running deploy-infra) — this workflow
// does not attempt to auto-restart anything, on purpose: an automated
// restart would defeat the entire point of a spend cap.
//
// WHAT THIS DOES NOT ELIMINATE: Service Bus (Standard tier), the shared
// Container Registry (Basic tier), Key Vault, and Storage all bill at
// fixed or usage-based rates independent of whether any Container App is
// running — stopping compute does not zero out the subscription's spend,
// it stops the (much larger) variable compute cost from growing further.
//
// CAVEATS THIS SESSION COULD NOT VERIFY LIVE (no Azure access from this
// sandboxed environment — see infra/modules/cost-management/main.bicep's
// header for the full list): the exact ARM PATCH merge semantics for
// Microsoft.App/containerApps (assumed to merge rather than replace
// `properties.template`, consistent with documented ARM PATCH behavior,
// but not exercised against a real deployment here), and the Action
// Group -> Logic App -> ManagedServiceIdentity chain end to end. STRONGLY
// recommend a human trigger this workflow manually once (via its Request
// trigger URL, e.g. with a test POST) during a planned, low-stakes window
// before trusting the automatic 100%-threshold path.

@description('Azure region.')
param location string = resourceGroup().location

@description('Postgres Flexible Server name to stop.')
param postgresServerName string

@description('Names of every Container App in cae-cmos-dev to scale to 0 replicas.')
param containerAppNames array

@secure()
@description('The teams-webhook-url Key Vault secret VALUE (never its URL) — resolved by the caller via an existing Key Vault resource\'s .getSecret() and passed straight into this workflow\'s own securestring parameter (see main.bicep\'s header for why this avoids a live Key-Vault-read action inside the workflow itself, and never needs a Key Vault role grant for this workflow\'s identity).')
param teamsWebhookUrl string

var subscriptionId = subscription().subscriptionId
var resourceGroupName = resourceGroup().name
var postgresStopUri = 'https://management.azure.com/subscriptions/${subscriptionId}/resourceGroups/${resourceGroupName}/providers/Microsoft.DBforPostgreSQL/flexibleServers/${postgresServerName}/stop?api-version=2023-06-01-preview'

resource emergencyShutoffWorkflow 'Microsoft.Logic/workflows@2019-05-01' = {
  name: 'la-cost-emergency-shutoff'
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    state: 'Enabled'
    parameters: {
      teamsWebhookUrl: {
        type: 'securestring'
        value: teamsWebhookUrl
      }
      containerAppNames: {
        type: 'array'
        value: containerAppNames
      }
    }
    definition: {
      '$schema': 'https://schema.management.azure.com/providers/Microsoft.Logic/schemas/2016-06-01/workflowdefinition.json#'
      contentVersion: '1.0.0.0'
      parameters: {
        teamsWebhookUrl: {
          type: 'securestring'
        }
        containerAppNames: {
          type: 'array'
        }
      }
      triggers: {
        manual: {
          type: 'Request'
          kind: 'Http'
          inputs: {
            schema: {}
          }
        }
      }
      actions: {
        StopPostgres: {
          type: 'Http'
          inputs: {
            method: 'POST'
            uri: postgresStopUri
            authentication: {
              type: 'ManagedServiceIdentity'
              audience: 'https://management.azure.com/'
            }
          }
          runAfter: {}
        }
        ScaleContainerAppsToZero: {
          type: 'Foreach'
          foreach: '@parameters(\'containerAppNames\')'
          actions: {
            ScaleOneContainerAppToZero: {
              type: 'Http'
              inputs: {
                method: 'PATCH'
                // Bicep-time string interpolation for the fixed parts of the
                // URI (subscription/resource group), workflow-time '@{...}'
                // expression for the per-iteration app name — the two
                // interpolation styles are evaluated at different times and
                // do not conflict.
                uri: 'https://management.azure.com/subscriptions/${subscriptionId}/resourceGroups/${resourceGroupName}/providers/Microsoft.App/containerApps/@{items(\'ScaleContainerAppsToZero\')}?api-version=2024-03-01'
                authentication: {
                  type: 'ManagedServiceIdentity'
                  audience: 'https://management.azure.com/'
                }
                headers: {
                  'Content-Type': 'application/json'
                }
                body: {
                  properties: {
                    template: {
                      scale: {
                        minReplicas: 0
                        maxReplicas: 0
                      }
                    }
                  }
                }
              }
              runAfter: {}
            }
          }
          runAfter: {}
        }
        NotifyShutoffExecuted: {
          type: 'Http'
          inputs: {
            method: 'POST'
            uri: '@parameters(\'teamsWebhookUrl\')'
            headers: {
              'Content-Type': 'application/json'
            }
            body: {
              text: 'CMOS cmos-dev: the USD $200/month budget cap was reached. Automated shutoff ran — Postgres has been stopped and every Container App scaled to 0 replicas. This is a FULL PLATFORM OUTAGE until a human manually restarts Postgres and scales Container Apps back up (e.g. by re-running deploy-infra). Check StopPostgres and ScaleContainerAppsToZero action results in this run\'s history for any failures.'
            }
          }
          // Fires regardless of whether the stop/scale actions actually
          // succeeded — knowing the shutoff RAN (even partially) matters
          // more than gating the notification on a clean result.
          runAfter: {
            StopPostgres: ['Succeeded', 'Failed', 'Skipped', 'TimedOut']
            ScaleContainerAppsToZero: ['Succeeded', 'Failed', 'Skipped', 'TimedOut']
          }
        }
      }
    }
  }
}

output workflowName string = emergencyShutoffWorkflow.name
output workflowId string = emergencyShutoffWorkflow.id
output principalId string = emergencyShutoffWorkflow.identity.principalId
output triggerCallbackUrl string = listCallbackUrl('${emergencyShutoffWorkflow.id}/triggers/manual', '2019-05-01').value
