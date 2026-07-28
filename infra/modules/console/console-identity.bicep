// Canvas Marketing OS — console-identity.bicep
//
// Creates the console's user-assigned managed identity as a genuine
// resource — never an `existing =` lookup. This eliminates the identity
// chicken-and-egg hazard architecturally: this module's principalId/
// clientId/id outputs are consumed by the registry-consumption module and
// console-app.bicep within the SAME atomic deployment (see infra/main.bicep's
// insertion point), so ARM resolves the dependency automatically — no
// preflight `az identity create`, no separate bootstrap job, no CI
// `needs:` ordering is required.
//
// This identity is also the subject of the console's App Registration's
// Federated Identity Credential (AUTH-001/AUTH-003) — see
// scripts/bootstrap-console-auth.sh, which reads this identity's
// principalId/clientId (read-only `az identity show`) to print the exact
// manual Entra Portal steps for a human with directory admin rights.

@description('Azure region.')
param location string = resourceGroup().location

@description('User-assigned managed identity name.')
param identityName string = 'id-console-cmos-dev'

resource consoleIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: identityName
  location: location
}

output id string = consoleIdentity.id
output principalId string = consoleIdentity.properties.principalId
output clientId string = consoleIdentity.properties.clientId
output name string = consoleIdentity.name
