// Canvas Marketing OS — infra/modules/pgbouncer-app.bicep
//
// ca-pgbouncer — TD-12 fix, part 2. One shared PgBouncer Container App in
// front of the single Postgres Flexible Server, sitting between it and the
// 6 long-running services that hold persistent connection pools and can
// each scale to 3 replicas (ca-model-gateway, ca-gatekeeper,
// ca-gatekeeper-approval, ca-publisher, ca-vault, ca-orchestrator — see
// infra/main.bicep's wiring). A dedicated Container App rather than a
// sidecar-per-service: this is a cross-service concern (one Postgres
// server, one connection ceiling), and a shared instance gives one place to
// tune pool sizing/observe connection pressure instead of six independent
// copies that could each be configured differently. One-shot migration/
// smoke-test/retention/secret-writer Container Apps Jobs are NOT routed
// through this — they run briefly, at parallelism 1, and were never the
// source of the replica-math connection pressure PERF-2 documented; keeping
// their DDL/admin work on a direct Postgres connection is simpler and lower
// risk than adding a proxy hop to jobs that don't need one.
//
// Internal-only TCP ingress (transport: 'tcp', external: false) — internal
// TCP ingress works without a custom VNet (learn.microsoft.com/azure/
// container-apps/connect-apps#transport-protocols: "Internal TCP ingress
// works without a custom VNet"), consistent with this repo's private-
// endpoint/in-VNet-only posture for anything on the data path (mirrors
// mcp/container-app.bicep's identical internal-only rationale). Other
// Container Apps in cae-cmos-dev reach it at
// `${containerApp.properties.configuration.ingress.fqdn}:6432` — read from
// the live resource, never a computed/guessed hostname (L-0025).
//
// Public MCR/Docker-Hub images are NOT used here — pgbouncer/Dockerfile
// builds a small image from a pinned Debian base + the apt pgbouncer
// package + our own entrypoint.sh (see that file for the pooling-mode
// rationale). Because it IS a CI-built image, the full L-0048/L-0060
// bootstrap contract applies and is carried in full from this app's first
// version, not as follow-up:
//   (1) `image` below defaults to a public MCR placeholder needing no
//       registry auth at all (main.bicep's pgbouncerContainerImage param).
//   (2) deploy-infra.yml's preflight resolves it to ca-pgbouncer's CURRENT
//       live image once one exists, never regressing a running app back to
//       placeholder — same "preserve live image" step every other CI-built
//       app in this template already gets.
//   (3) This module never declares `registries[]` (L-0049/L-0060: a
//       user-assigned identity newly attached AND referenced by
//       `registries[].identity` in the SAME initial create call is a
//       confirmed Azure platform limitation, microsoft/azure-container-
//       apps#1467) — deploy-pgbouncer.yml's `az containerapp registry set`
//       is the sole, exclusive setter, every run, before the image update.
//   (4) deploy-pgbouncer.yml is the ONLY thing that ever calls
//       `az containerapp update --image`, pinned to a commit SHA, and
//       resolves its target registry from container-registry.bicep's own
//       deployment record (`az deployment group show -n container-registry`),
//       never an ambient `az acr list [0]` pick (L-0021).
//
// 1 replica, not 2+ (revised after TD-12's tier/HA change was reverted for
// budget — see postgres.bicep's header): Postgres is back on Burstable
// B1ms, whose max_connections=50 (35 usable after Azure's 15 reserved) is
// tight enough that multiple PgBouncer replicas each maintaining an
// independent default_pool_size would multiply the worst-case backend
// connection count in a way that's genuinely hard to keep safely under 35 —
// one replica means one pool to reason about. Burstable also has no HA of
// its own, so a single PgBouncer replica isn't trading away redundancy this
// environment actually has elsewhere. default_pool_size is deliberately
// small (see its own param comment) to leave headroom for the one-shot
// jobs that connect directly to Postgres, bypassing this app entirely.

@description('Azure region.')
param location string = resourceGroup().location

@description('Container App name.')
param appName string = 'ca-pgbouncer'

@description('Resource id of the Container Apps managed environment (cae-cmos-dev).')
param environmentId string

@description('Full container image reference. Defaults to a public MCR placeholder at the call site (main.bicep) — see the bootstrap-contract header comment above. Only deploy-pgbouncer.yml (via `az containerapp update --image`, after `az containerapp registry set`) ever sets a real, SHA-pinned image.')
param image string

@description('Resource id of the user-assigned managed identity this app runs as. ACR pull is attached exclusively by deploy-pgbouncer.yml via `az containerapp registry set`, never by this template — see the header comment above.')
param userAssignedIdentityId string

@description('Postgres server fully-qualified domain name (the REAL server, never this app itself) — infra/main.bicep passes postgres.outputs.fqdn.')
param postgresFqdn string

@description('Postgres administrator login.')
param administratorLogin string

@secure()
@description('Postgres administrator login password.')
param administratorLoginPassword string

@description('Postgres database name every service connects to (this repo runs one physical database, many schemas — see orchestrator/db.py\'s own comment on this).')
param databaseName string = 'postgres'

@description('Port PgBouncer listens on and clients connect to.')
param listenPort int = 6432

@description('PgBouncer pool_mode. session, not transaction — see entrypoint.sh\'s header for why this is load-bearing (orchestrator\'s and model-gateway\'s session-scoped pg_advisory_lock usage).')
param poolMode string = 'session'

@description('Max simultaneous PgBouncer client connections, per replica.')
param maxClientConn int = 1000

@description('Max simultaneous PgBouncer->Postgres backend connections — the real enforced ceiling TD-12 needed. With a single replica (see header) this IS the worst case against Postgres. Sized against Burstable B1ms\'s 35 usable connections (50 max_connections - Azure\'s 15 reserved for replication/monitoring): 25 here leaves ~10 free for the one-shot migration/smoke-test/retention/secret-writer jobs that connect directly to Postgres, bypassing this app. Raise this only alongside a Postgres tier upgrade that actually has the headroom for it (see postgres.bicep\'s header on why General Purpose was reverted).')
param defaultPoolSize int = 25

@description('Changes on every deploy (main.bicep defaults it to utcNow()) so this app always gets a NEW revision. Same governance-round-4 pattern as every other *-app.bicep in this repo: with activeRevisionsMode Single, a redeploy that only changes a secret VALUE (e.g. a rotated Postgres admin password) does NOT create a new revision — the already-running replica keeps stale backend credentials indefinitely. Forcing a fresh revisionSuffix every deploy is what actually restarts the container and picks up current values.')
param deployToken string

resource containerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: appName
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${userAssignedIdentityId}': {}
    }
  }
  properties: {
    environmentId: environmentId
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: false
        targetPort: listenPort
        transport: 'tcp'
      }
      // Deliberately NO registries[] block — see the bootstrap-contract
      // header comment on the `image` param above. deploy-pgbouncer.yml's
      // `az containerapp registry set` owns this exclusively once the app
      // exists.
      secrets: [
        {
          name: 'postgres-admin-password'
          value: administratorLoginPassword
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'pgbouncer'
          image: image
          env: [
            {
              name: 'POSTGRES_HOST'
              value: postgresFqdn
            }
            {
              name: 'POSTGRES_PORT'
              value: '5432'
            }
            {
              name: 'POSTGRES_DB'
              value: databaseName
            }
            {
              name: 'POSTGRES_ADMIN_USER'
              value: administratorLogin
            }
            {
              name: 'POSTGRES_ADMIN_PASSWORD'
              secretRef: 'postgres-admin-password'
            }
            {
              name: 'PGBOUNCER_LISTEN_PORT'
              value: string(listenPort)
            }
            {
              name: 'PGBOUNCER_POOL_MODE'
              value: poolMode
            }
            {
              name: 'PGBOUNCER_MAX_CLIENT_CONN'
              value: string(maxClientConn)
            }
            {
              name: 'PGBOUNCER_DEFAULT_POOL_SIZE'
              value: string(defaultPoolSize)
            }
          ]
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 1
      }
      revisionSuffix: 'r${uniqueString(deployToken)}'
    }
  }
}

output appId string = containerApp.id
output appName string = containerApp.name
output internalFqdn string = containerApp.properties.configuration.ingress.fqdn
