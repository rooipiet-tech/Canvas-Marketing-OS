// Canvas Marketing OS — postgres.bicep
// Postgres Flexible Server 16, public network access Disabled, reachable
// only via a private endpoint in snet-pe. Password-based admin auth
// (administratorLoginPassword @secure(), no default — supplied at deploy
// time by the workflow; no Key Vault round-trip in this credential's
// critical path). No dependsOn relationship to key-vault.bicep.
//
// TD-12 fix (docs/architecture/09-technical-debt.md): General Purpose tier
// + zone-redundant HA, replacing the original Standard_B1ms Burstable
// single-zone server. Burstable cannot carry HA at all — confirmed against
// Microsoft's own docs (learn.microsoft.com/azure/reliability/
// reliability-database-postgresql#resilience-to-availability-zone-failures,
// learn.microsoft.com/azure/postgresql/high-availability/
// how-to-configure-high-availability#limitations-and-considerations: "The
// Burstable tier doesn't support high availability. Only the General
// purpose and Memory optimized tiers support high availability.") — General
// Purpose is a prerequisite for HA on Flexible Server, not an independent
// upgrade alongside it. Standard_D2ds_v5 (2 vCores, 8 GiB) is the smallest
// General Purpose SKU and raises the platform default connection ceiling
// from B1ms's 50 (35 usable after Azure's 15 reserved) to 859 (844 usable)
// — see learn.microsoft.com/azure/postgresql/configure-maintain/
// concepts-limits#maximum-connections. This is what actually removes the
// ceiling PERF-2 hit (vault/db.py's pool shrink, 3 replicas × 20 = 60 >
// max_connections=50); PgBouncer (infra/modules/pgbouncer-app.bicep) adds a
// second, independent layer of protection on top so no single service's
// pool-size math can exhaust the server again as replica counts grow.

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

@description('Availability zone the primary server runs in.')
param availabilityZone string = '1'

@description('Availability zone the HA standby runs in. Must differ from availabilityZone — zone-redundant HA places the standby in a different zone than the primary (learn.microsoft.com/azure/postgresql/high-availability/concepts-high-availability#availability-zone-support-types).')
param standbyAvailabilityZone string = '2'

resource postgres 'Microsoft.DBforPostgreSQL/flexibleServers@2023-06-01-preview' = {
  name: serverName
  location: location
  sku: {
    name: 'Standard_D2ds_v5'
    tier: 'GeneralPurpose'
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
    highAvailability: {
      mode: 'ZoneRedundant'
      standbyAvailabilityZone: standbyAvailabilityZone
    }
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
