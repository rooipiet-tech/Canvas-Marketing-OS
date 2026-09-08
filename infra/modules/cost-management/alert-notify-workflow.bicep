// Canvas Marketing OS — infra/modules/cost-management/alert-notify-workflow.bicep
//
// la-cost-alert-notify — the alert-only half of the USD $200/month budget
// cap (see budget.bicep, main.bicep). Posts a Teams message; takes no
// destructive action. Shared by BOTH the 50% and 80% budget notifications
// — distinguished by a `threshold` query-string parameter on each Action
// Group's own callback URL (`ag-cost-alert-50`/`ag-cost-alert-80` in
// budget.bicep each append their own `?threshold=50`/`?threshold=80` to
// the SAME workflow trigger URL), read via the Request trigger's
// `queries` output. This avoids depending on the exact shape of Azure
// Monitor's budget-alert payload (not verified live in this session) to
// tell the two thresholds apart.

@description('Azure region.')
param location string = resourceGroup().location

@secure()
@description('The teams-webhook-url Key Vault secret VALUE — see emergency-shutoff-workflow.bicep\'s identical param for why this is passed in rather than read live from Key Vault.')
param teamsWebhookUrl string

resource alertNotifyWorkflow 'Microsoft.Logic/workflows@2019-05-01' = {
  name: 'la-cost-alert-notify'
  location: location
  properties: {
    state: 'Enabled'
    parameters: {
      teamsWebhookUrl: {
        type: 'securestring'
        value: teamsWebhookUrl
      }
    }
    definition: {
      '$schema': 'https://schema.management.azure.com/providers/Microsoft.Logic/schemas/2016-06-01/workflowdefinition.json#'
      contentVersion: '1.0.0.0'
      parameters: {
        teamsWebhookUrl: {
          type: 'securestring'
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
        PostToTeams: {
          type: 'Http'
          inputs: {
            method: 'POST'
            uri: '@parameters(\'teamsWebhookUrl\')'
            headers: {
              'Content-Type': 'application/json'
            }
            body: {
              text: 'CMOS cmos-dev cost alert: @{coalesce(triggerOutputs()[\'queries\'][\'threshold\'], \'an unspecified\')}% of the USD $200/month infra budget has been used. Review Azure Cost Management before it reaches 100% and triggers automated shutoff (Postgres stop + every Container App scaled to 0).'
            }
          }
          runAfter: {}
        }
      }
    }
  }
}

output workflowName string = alertNotifyWorkflow.name
output workflowId string = alertNotifyWorkflow.id
output triggerCallbackUrl string = listCallbackUrl('${alertNotifyWorkflow.id}/triggers/manual', '2019-05-01').value
