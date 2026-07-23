// Canvas Marketing OS — service-bus.bicep
//
// LOCKED DECISION (budget owner, spec v2 / INFRA-006): Standard SKU, no
// private endpoint, for the dev environment. Compensating controls:
// disableLocalAuth=true (Entra managed-identity auth only, SAS disabled),
// minimumTlsVersion=1.2. Risk recorded in docs/accepted-risks.md;
// redaction/metadata-only compensating control documented in
// contracts/service-bus/spec.md. Do not add a private endpoint here —
// that is the locked production hardening path, not this build.

@description('Azure region.')
param location string = resourceGroup().location

@description('Base name used to derive a globally-unique Service Bus namespace name.')
param namePrefix string = 'sb-cmos-dev'

var namespaceName = '${namePrefix}-${uniqueString(resourceGroup().id)}'

resource serviceBusNamespace 'Microsoft.ServiceBus/namespaces@2022-10-01-preview' = {
  name: namespaceName
  location: location
  sku: {
    name: 'Standard'
    tier: 'Standard'
  }
  properties: {
    disableLocalAuth: true
    minimumTlsVersion: '1.2'
  }
}

resource taskQueue 'Microsoft.ServiceBus/namespaces/queues@2022-10-01-preview' = {
  parent: serviceBusNamespace
  name: 'task'
  properties: {
    lockDuration: 'PT1M'
    maxDeliveryCount: 10
    deadLetteringOnMessageExpiration: true
  }
}

resource eventQueue 'Microsoft.ServiceBus/namespaces/queues@2022-10-01-preview' = {
  parent: serviceBusNamespace
  name: 'event'
  properties: {
    lockDuration: 'PT1M'
    maxDeliveryCount: 10
    deadLetteringOnMessageExpiration: true
  }
}

output namespaceName string = serviceBusNamespace.name
output namespaceId string = serviceBusNamespace.id
