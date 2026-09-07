// Canvas Marketing OS — postgres.bicep
// Postgres Flexible Server 16, public network access Disabled, reachable
// only via a private endpoint in snet-pe. Password-based admin auth
// (administratorLoginPassword @secure(), no default — supplied at deploy
// time by the workflow; no Key Vault round-trip in this credential's
// critical path). No dependsOn relationship to key-vault.bicep.
//
// TD-12 (docs/architecture/09-technical-debt.md): General Purpose tier +
// zone-redundant HA (Standard_D2ds_v5) shipped and merged in PR #183, then
// REVERTED here in the same session once a real budget constraint surfaced
// (a ZAR 3000/month total infra cap) — General Purpose alone runs
// materially over that on its own (~$131/mo baseline before HA doubles it,
// per third-party estimates; this session could not reach Azure's own
// pricing API/calculator to get a live figure — see the PR history).
// Burstable cannot carry HA at all regardless of budget (confirmed against
// Microsoft's own docs: learn.microsoft.com/azure/postgresql/
// high-availability/how-to-configure-high-availability#limitations-and-considerations,
// "The Burstable tier doesn't support high availability."), so TD-12's
// connection-ceiling (max_connections=50, PERF-2's root cause) and
// no-HA problems both remain OPEN, gated on a future budget increase — not
// silently dropped. PgBouncer (infra/modules/pgbouncer-app.bicep) still
// ships and still helps: it enforces a real ceiling on backend connections
// regardless of Postgres tier, so a service's own pool-size math can no
// longer exhaust the server on its own the way PERF-2 did — see that
// module's header for how its pool sizing was re-tuned for Burstable's much
// tighter 35-usable-connection budget specifically.

@description('Azure region.')
param location string = resourceGroup().location

@description('Postgres flexible server name.')
param serverName string = 'psql-cmos-dev'

@description('Non-secret administrator login name.')
param administratorLogin string = 'cmosadmin'

@secure()
@description('Administrator login password. No default — must be supplied at deploy time.')
param administratorLoginPassword string

@description('Resource id of the subnet private endpoints are created in.')
param privateEndpointSubnetId string

@description('Resource id of the privatelink.postgres.database.azure.com private DNS zone.')
param postgresPrivateDnsZoneId string

@description('Availability zone the server runs in.')
param availabilityZone string = '1'

resource postgres 'Microsoft.DBforPostgreSQL/flexibleServers@2023-06-01-preview' = {
  name: serverName
  location: location
  sku: {
    name: 'Standard_B1ms'
    tier: 'Burstable'
  }
  properties: {
    version: '16'
    administratorLogin: administratorLogin
    administratorLoginPassword: administratorLoginPassword
    storage: {
      storageSizeGB: 32
    }
    backup: {
      backupRetentionDays: 7
      geoRedundantBackup: 'Disabled'
    }
    network: {
      publicNetworkAccess: 'Disabled'
    }
    availabilityZone: availabilityZone
  }
}

resource postgresPrivateEndpoint 'Microsoft.Network/privateEndpoints@2023-09-01' = {
  name: 'pe-${serverName}'
  location: location
  properties: {
    subnet: {
      id: privateEndpointSubnetId
    }
    privateLinkServiceConnections: [
      {
        name: 'pe-${serverName}-connection'
        properties: {
          privateLinkServiceId: postgres.id
          groupIds: [
            'postgresqlServer'
          ]
        }
      }
    ]
  }
}

resource postgresPrivateDnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-09-01' = {
  parent: postgresPrivateEndpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'postgres-config'
        properties: {
          privateDnsZoneId: postgresPrivateDnsZoneId
        }
      }
    ]
  }
}

output serverName string = postgres.name
output serverId string = postgres.id
output fqdn string = postgres.properties.fullyQualifiedDomainName
