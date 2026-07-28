# Accepted risks

This document records risks explicitly accepted by the budget owner for
the `cmos-dev` environment, along with their compensating controls and
production hardening path. It is not a substitute for a formal risk
register or POPIA compliance sign-off (see `docs/credentials-runbook.md`
and `.loop/spec.json` out_of_scope) — technical controls recorded here are
enablers, not full compliance.

## Risk: Service Bus dev namespace has no private endpoint

- **Component**: Azure Service Bus namespace (`infra/modules/service-bus.bicep`).
- **Decision**: For the `cmos-dev` environment, Service Bus is deployed as
  **Standard SKU** with **no private endpoint** — reachable over its
  public endpoint — instead of Premium SKU + private endpoint.
- **Decided by**: budget owner (see `.loop/spec.json` `locked_decisions`,
  amendment v2, INFRA-006).
- **Reason**: Premium SKU (required for private-endpoint support) is not
  budget-justified for a dev environment at current scale.

### Compensating controls

1. **`disableLocalAuth = true`** — SAS-key authentication is disabled;
   only Entra ID (managed identity) authentication is accepted.
2. **`minimumTlsVersion = '1.2'`** — all connections are TLS 1.2+ only.
3. **Metadata-only envelopes** — queue messages carry task metadata only
   (ids, routing hints); client/personal data is never placed on the
   queue. Redaction rules are documented in
   `contracts/service-bus/spec.md`.

Taken together, these controls mean that even though the namespace is
reachable from the public internet, an attacker without a valid Entra
identity cannot authenticate, and even a successful read of a message
would expose only task metadata, never client or personal data.

### Production hardening path

The Service Bus dev risk acceptance above is explicitly **not** the
target production posture. Before this system handles production
traffic, Service Bus must be upgraded to:

- **Premium** SKU (required to support private endpoints), and
- a **private endpoint** into the VNet, removing public network
  reachability entirely — matching the posture already applied to
  Postgres, Key Vault, and Storage in this build.

This upgrade is deliberately deferred out of this build's scope; it is
tracked here so it is not forgotten before any production rollout.

## Risk: Shared Container Registry runs Basic SKU with admin access disabled

- **Component**: Azure Container Registry
  (`infra/modules/container-registry.bicep`) — the single canonical shared
  ACR for the whole platform, consumed by every service that needs to push
  or pull images.
- **Decision**: **Basic SKU**, no geo-replication, no content-trust or
  vulnerability-scanning add-ons, and **`adminUserEnabled = false`**. This
  carries a small standing monthly cost even when idle.
- **Decided by**: budget owner (see `.loop/research.md` locked decision #3 —
  "Basic SKU ACR, admin account disabled, pull via managed identity
  (cheapest viable option; note standing cost in `docs/accepted-risks.md`)").
- **Reason**: Basic is the cheapest SKU that still supports Entra
  managed-identity image pull, which is what lets the registry hold no
  shared static credential at all. Higher SKUs buy geo-replication and
  scanning features a single-service dev registry cannot justify.

### Compensating controls

1. **Admin account disabled** — there is no username/password pair for this
   registry, so no shared static credential exists to leak or rotate.
2. **Explicit `AcrPull` role assignment** — the gateway's user-assigned
   managed identity (`gateway.bicep`; see operational note 1 below for why
   not system-assigned) is granted pull rights by an explicit role
   assignment in the same module; pull access is therefore an auditable
   Entra grant, not a possessed secret.
3. **Push happens only via OIDC** — `.github/workflows/deploy-gateway.yml`
   authenticates with the existing federated credential
   (`AZURE_CLIENT_ID`/`AZURE_TENANT_ID`/`AZURE_SUBSCRIPTION_ID`); the
   workflow contains and creates no registry credential.

### Production hardening path

Before any production rollout: Standard or Premium SKU, geo-replication for
the serving region(s), and Microsoft Defender for Containers vulnerability
scanning on pushed images. Deliberately deferred here, recorded so it is not
forgotten.

### Four operational notes for whoever runs the first real deploy

1. **First-provision bootstrap image and identity (fix/deploy-infra-gateway,
   two rounds).**
   `ca-model-gateway` no longer needs the shared ACR to already contain an
   image on first provision. `gateway.bicep`'s `containerImage` parameter
   defaults to a public, unauthenticated MCR quickstart image
   (`mcr.microsoft.com/azuredocs/containerapps-helloworld:latest`), and
   `deploy-infra.yml`'s preflight resolves it to the app's CURRENT live
   image on every subsequent run (via `az containerapp show`) — but only if
   `properties.latestReadyRevisionName` is non-empty, i.e. the app has
   actually produced a healthy revision at least once; otherwise it
   bootstraps with the placeholder again rather than replaying an image
   reference that has never worked. `deploy-gateway.yml` remains the only
   thing that ever sets a real image, via `az containerapp update --image`.
   (Round 1 fixed an "expected transient unhealthy revision" ordering bug:
   `gateway.bicep` depended on a separately-computed registry-name string
   instead of the container-registry module's real output, so ARM had no
   guarantee the registry existed before the Container App tried to pull
   from it, failing deploy-infra #10 with
   `failed to resolve registry ... no such host`. Round 2 fixed a second,
   deeper bug the first fix exposed: the gateway's `AcrPull` and
   `Key Vault Secrets User` role assignments read a **system**-assigned
   identity's `principalId`, which Bicep can only resolve once the Container
   App's own deployment reaches a terminal state — but that terminal state
   requires a healthy first revision, which needs those very role
   assignments. Confirmed live via `az deployment operation group list` (the
   app sat `Failed`/"Operation expired" for 20+ minutes) and
   `az role assignment list --assignee <principalId>` (came back empty — the
   role assignments never got a chance to deploy). Fixed by switching to a
   `Microsoft.ManagedIdentity/userAssignedIdentities` resource, whose
   `principalId` is available synchronously with no dependency on the
   Container App at all, breaking the cycle.)
2. **`cmos-dev` holds a second, orphaned Container Registry
   (`acrcmosdev...`) that is not declared anywhere in `infra/` — do not let
   tooling pick a registry by list order.**
   Live diagnosis of a `deploy-gateway` UNAUTHORIZED pull failure found two
   `Microsoft.ContainerRegistry` resources in the resource group: the
   Bicep-managed canonical one (named `acrcmosshared<uniqueString>` by
   `main.bicep`, the only one `gateway.bicep` grants `AcrPull` to or binds
   into `ca-model-gateway`'s `registries[]`), and an orphaned legacy one
   (`acrcmosdev<uniqueString>`, already holding stale `model-gateway` and
   `vault` repositories from before the "shared ACR" naming was adopted).
   `deploy-gateway.yml` used to resolve its target registry with
   `az acr list -g cmos-dev --query "[0].loginServer"`, which is
   order-dependent with two registries present and silently resolved to the
   orphan — the image built and pushed there without error, but
   `ca-model-gateway`'s identity has no `AcrPull` grant or `registries[]`
   credential for that server at all, so the subsequent pull failed
   `UNAUTHORIZED`. Fixed by resolving the login server from
   `ca-model-gateway`'s own live `properties.configuration.registries[0].server`
   instead (the value `gateway.bicep` itself set), which is guaranteed to
   match by construction. The orphaned registry itself was left in place
   (not deleted) — deleting a live Azure resource wasn't requested and is
   flagged here rather than done unilaterally; it holds no traffic (nothing
   pulls from it) and can be removed by a human operator once confirmed
   unneeded elsewhere.
3. **`Microsoft.ContainerRegistry` may not be registered.**
   `deploy-infra.yml`'s preflight registers/verifies `Microsoft.App`,
   `Microsoft.DBforPostgreSQL` and `Microsoft.ServiceBus` only, and that
   workflow is outside this build's locked touch-scope, so the ACR resource
   provider is never checked. If this subscription has never used a
   container registry, the first deployment referencing this module may fail
   with a `MissingSubscriptionRegistration`-style error. Fix by running
   `az provider register --namespace Microsoft.ContainerRegistry` and
   retrying. Documentation-only mitigation — no in-scope code change can
   close it.
4. **`az containerapp job start` with `--env-vars` does not "add an env var
   to the existing template" — it replaces the whole container.**
   Live diagnosis of a `deploy-gateway` `ContainerAppImageRequired` failure
   on `caj-vault-query`'s "Seed a real agent_run" step found the job's Bicep
   template already had a perfectly valid default image
   (`vault-query-job.bicep`'s `image: 'postgres:16'`, confirmed via
   `az containerapp job show`). The failure was purely a CLI invocation bug:
   `az containerapp job start` only inherits the template's container as-is
   when invoked with **zero** Container Arguments; passing any of them
   (`--env-vars`, `--command`, `--cpu`, etc.) switches the CLI into an
   override mode that constructs a brand-new container spec from scratch,
   dropping everything not explicitly re-supplied — confirmed live that
   `--env-vars QUERY=...` alone fails outright
   (`ERROR (ContainerAppImageRequired)`), and that adding `--image` back
   still silently drops the template's `command` (the `psql` invocation)
   and the `DATABASE_URL` secretRef, leaving a container that just runs
   `postgres:16`'s bare entrypoint. Fixed by having `deploy-gateway.yml`
   build a full `--yaml` override per invocation (image + command + both
   env vars restated, only the `QUERY` value changed) instead of
   `--env-vars`; verified live end-to-end against `cmos-dev` — a real
   `INSERT ... RETURNING id` and a follow-up `SELECT` confirming the row
   both completed with job status `Succeeded` through the `--yaml` path.
   `vault-query-job.bicep` itself needed no changes — its default image was
   never the problem.

## Risk: Vault taxonomy/consent/retention/rollup bookkeeping lives in a separate `vault_internal` schema, not the frozen public schema

- **Component**: Vault service (`services/vault`), sidecar migration
  (`services/vault/migrations/0001_vault_internal_init.sql`,
  `infra/modules/vault/sidecar-migration-job.bicep`).
- **Decision**: taxonomy fields, consent linkage, retention policy, and
  utilisation roll-ups are persisted in a new Postgres schema,
  `vault_internal`, in the same database instance as the frozen `public`
  schema — rather than as new columns/tables inside
  `contracts/vault-schema/schema.sql`.
- **Decided by**: user, 2026-07-28 (see `.loop/spec.json`
  `resolved_decisions` `OQ-1..4-RESOLVED`).
- **Reason**: `contracts/vault-schema/schema.sql` is frozen and guarded by
  a breaking-change hash (`contracts/.frozen-v1.sha256`,
  `scripts/validate_contracts.py`) — any `ALTER TABLE`/DDL change to its
  9 tables is a hard stop for this build. Taxonomy/consent/retention/
  rollup bookkeeping is real, necessary functionality for the Vault
  service, so it ships now as additive `vault_internal` sidecar tables
  rather than being blocked on a frozen-schema amendment process.

### Compensating controls

1. **`campaign` is still persisted to a real public-schema column**
   where one exists (`campaign_id` on `opportunity_cards`, `briefs`,
   `assets`, `agent_runs` — see `AC-003`) — only the other 5 taxonomy
   fields, consent linkage, retention policy, and utilisation rollups
   live exclusively in `vault_internal`.
2. **The contract never leaks the split**: `contracts/vault-api.yaml`
   presents taxonomy fields, consent linkage, and rollups as first-class
   request/response concepts on the object itself — no `vault_internal`
   or "sidecar" reference anywhere in the document (`AC-023`,
   `scripts/validate_contracts.py`'s `check_no_internal_leak`).
3. **Same migration rigor as the frozen schema**: `vault_internal`'s DDL
   is a versioned, idempotent migration, tested in CI against a
   disposable Postgres instance (twice in a row, for idempotency), and
   applied through the same in-VNet Container Apps Job mechanism as
   `caj-vault-migrate` (`caj-vault-sidecar-migrate`, including the
   identical base64-encoding fix for `$$` PL/pgSQL dollar-quoting).

### Production hardening path

Consolidating `vault_internal` into a proper v2 schema — either folding
its tables into a version-bumped `contracts/vault-schema/schema.sql`, or
formally freezing `vault_internal` itself as a second frozen-baseline
file guarded by its own hash — is a **deferred decision for the first
contract-revision window**, not attempted in this build. Until that
window, `vault_internal` remains a Vault-service-owned, additive sidecar
schema: safe to extend, but not to be treated as a permanent
architectural split that other services should also route bookkeeping
through.

## Retrieving Container Apps Job output (caj-vault-migrate / caj-vault-query)

- **Execution status** (stable CLI, no extension required):
  `az containerapp job execution list -g cmos-dev -n <job-name>`
- **Log content** (requires the `containerapp` CLI extension, bootstrapped
  non-interactively in the `preflight` job of `deploy-infra.yml`):
  `az containerapp job logs show -g cmos-dev -n <job-name>`
