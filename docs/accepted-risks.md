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
   **RESOLVED (2026-07-31):** verified via the `deploy-infra` ACR-verification gate; `acrcmosdevdziw5kptw2qee` deleted by the operator.
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
5. **Vault's own migration onto the canonical shared ACR (this session).**
   Vault was originally provisioned against a session-local, pre-convention
   registry (a copy of `container-registry.bicep` with `namePrefix =
   'acrcmosdev'`) — this is the very `acrcmosdev...` registry operational
   note 2 above found orphaned and holding stale `vault` repos. On rebase,
   that copy was dropped in favor of main's single canonical
   `container-registry.bicep` module (the same instance `ca-model-gateway`
   already consumes), and `infra/modules/vault/main.bicep` now binds its
   `acrLoginServer`/`acrRegistryName`/`acrRegistryId` params to that same
   module's outputs instead of owning a second registry. `.github/workflows/
   vault-image.yml` was fixed in the same change to resolve its push target
   from the `container-registry` module's own deployment output
   (`az deployment group show -g cmos-dev -n container-registry --query
   properties.outputs.loginServer.value`) rather than `az acr list --query
   "[0].name"` (the same L-0021 order-dependent anti-pattern
   `deploy-gateway.yml` had) — and deliberately NOT from `ca-vault`'s own
   `registries[]` binding the way `deploy-gateway.yml` resolves for
   `ca-model-gateway`: `ca-vault` is the resource being migrated, so its live
   binding still points at the old registry until a `deploy-infra` run
   actually repoints it, and reading it back at push time would just keep
   targeting the old registry. `deploy-infra.yml`'s vault deploy sequence
   now includes an explicit "Verify ca-vault is running from the canonical
   shared ACR" step (checks the live image reference against the
   `container-registry` output and that `latestReadyRevisionName` matches
   `latestRevisionName`) — this is the gate that must pass, on a real
   deploy, before `acrcmosdev...` is safe to delete.
   **RESOLVED (2026-07-31):** verified via the `deploy-infra` ACR-verification gate; `acrcmosdevdziw5kptw2qee` deleted by the operator.

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

## Risk: Vault API has no authentication/authorization on any endpoint

- **Component**: Vault service (`services/vault`), all routers
  (`services/vault/vault/routers/*.py`), including `consent_register`
  read/write/revoke and the retention-expiry/utilisation-rollup trigger
  endpoints.
- **Decision**: for this patch cycle, the Vault API ships with zero
  authentication or authorization on any endpoint. The sole access
  control is network isolation — the Container App
  (`infra/modules/vault/container-app.bicep`) uses internal-only ingress,
  VNet-integrated into `cae-cmos-dev`, so the API is unreachable from the
  public internet regardless.
- **Decided by**: builder judgment call during the s2-vault PATCH build
  (`.loop/review.json` risk-security findings RS-02/RS-03), 2026-07-28.
  Not a user-approved risk acceptance in the same sense as the other
  entries in this document — flagged here explicitly so it is tracked
  rather than silently shipped, pending an explicit budget-owner
  decision.
- **Reason**: adding real authentication (a shared-secret bearer token
  sourced from Key Vault, or full authn/authz) touches more than the
  Vault service code itself — it requires a new Key Vault secret, changes
  to `infra/modules/vault/secret-writer-job.bicep` to provision and
  rotate it, container-app env var/secretRef wiring, and updates to
  `infra/modules/vault/smoke-test-job.bicep` plus
  `services/vault/tests/test_contract_smoke.py` to authenticate every
  call. That is bigger than a targeted bug fix and risks destabilizing a
  build that is already carrying substantial other fixes in the same
  patch cycle, so it was deliberately deferred rather than added in a
  rush.

### Compensating controls

1. **Network isolation only**: internal Container Apps ingress + VNet
   integration means the API is not reachable from the public internet
   under any circumstance; only workloads already inside `cae-cmos-dev`'s
   VNet (or resources explicitly peered/routed into it) can reach it at
   all.
2. **Postgres and Key Vault stay network-isolated too**: even a caller
   that reached the Vault API could not pivot to the database or Key
   Vault directly — both remain `publicNetworkAccess=Disabled`
   (AC-011/AC-012), so the Vault API is genuinely the only path to this
   data, and that path currently has no identity check.
3. **X-Caller-Service header is informational only, not a trust
   boundary**: it feeds `vault_internal.access_log`/utilisation rollups
   for observability, but nothing enforces that callers set it honestly
   (`RS-04`) — it must not be treated as an authentication signal.

### Production hardening path

Before this system handles production traffic or is reachable from
outside a tightly-controlled VNet, the Vault API must gain real
authentication — at minimum a shared-secret bearer token validated by a
FastAPI dependency against a secret sourced from Key Vault the same way
`vault-db-connection-string` is loaded (`docs/credentials-runbook.md`),
ideally full Entra ID / managed-identity-based service-to-service auth
for parity with how the Vault service itself reaches its own
dependencies. This is deliberately deferred out of this patch cycle; it
is tracked here so it is not forgotten before any production rollout or
before any relaxation of the current internal-ingress-only network
posture.

## Risk: Registry artefact is signed with a committed development key

- **Component**: Function-definition registry signing key
  (`services/registry/keys/dev-signing-key.priv` / `.pub`, consumed by
  `services/registry/signing.py`).
- **Decision**: For this build, the registry artefact (`registry.json` +
  detached `registry.json.sig`) is signed with an **Ed25519 keypair
  committed to this repository**, instead of a production signing key held
  in Key Vault.
- **Decided by**: budget/scope owner (see `.loop/spec.json` AC-04, AC-05,
  AC-26 and the `out_of_scope` entry "Populating the Key Vault-held
  production signing key itself").
- **Reason**: Key Vault `kv-cmos-dev-dziw5kptw2qe` has
  `publicNetworkAccess = Disabled` and there is no in-VNet CI runner, so no
  Key Vault-held key is reachable from any execution environment in this
  scope. The alternative — shipping the artefact unsigned, or with a
  symmetric/`alg: none` construction — would be strictly worse, because the
  verification code path would then not exist at all and could not be
  swapped later without a code change.

### Compensating controls

1. **Unmistakable labelling** — the key is named `dev-signing-key.*` and
   `services/registry/keys/README.md` states in its first line that it
   confers **no security** and must never be used in production.
2. **Runtime warning on every use** — `signing.py` prints an explicit
   `WARNING:` line to stderr *every time* the dev-key fallback is actually
   used, in both signing and verification. A static README note is not
   enough; the warning appears in every CI run's output.
3. **Config-first key resolution** — the resolution order is
   `REGISTRY_SIGNING_KEY_PATH` / `REGISTRY_SIGNING_PUBLIC_KEY_PATH` env var
   first, committed dev key only as a fallback. Moving to a real key is a
   configuration swap, not a code change. A value beginning with
   `keyvault://` is recognised and **fails loudly** rather than silently
   degrading to the dev key.
4. **Asymmetric-only, algorithm-pinned** — Ed25519 is pinned and type-checked
   on load; there is no `alg: none` branch and no HMAC/shared-secret
   acceptance path anywhere in the signing or verification code, matching the
   convention already frozen in `contracts/gate-token/spec.md`.
5. **Not a client secret** — the committed key is asymmetric and public by
   construction. It carries no client, personal or credential data, so its
   disclosure discloses nothing that was not already intended to be public.

Taken together, these controls mean the committed key cannot be mistaken for
a real one, cannot silently substitute for one that was explicitly
configured, and cannot be used to downgrade the signature algorithm.

### Production hardening path

This risk acceptance is explicitly **not** the target production posture. As
a named follow-up, before the registry artefact is consumed by anything that
trusts its signature:

- **Algorithm correction (added 2026-07-31, see `L-0031`)**: the original
  plan below to generate the production key *as an Ed25519 key inside Key
  Vault* is not achievable — Azure Key Vault's standard tier supports only
  RSA (RS256/PS256) and EC P-256/P-384/P-521/P-256K (ES256/ES384/ES512)
  key types; it cannot create, sign, or verify EdDSA/Ed25519 keys at all.
  The production path therefore needs one of:
  (a) switch `signing.py`/`verify_signature.py` to an EC algorithm (ES256
  recommended) with the production key generated and held inside Key
  Vault, requiring a small code change to the signing/verification
  algorithm (not just a config swap as originally assumed) — the dev
  fallback key would also need to become EC-based to keep both paths
  algorithm-consistent; or
  (b) generate the Ed25519 key outside Key Vault and store the private key
  material as a Key Vault **secret** (not a **key**), which keeps it out
  of the repository but forgoes HSM-backed key protection and in-vault
  signing operations.
  Option (a) is the safer default; do not restart this follow-up without
  picking one explicitly.
- Grant the CI identity access via **OIDC federated identity** and the
  existing `AZURE_CLIENT_ID` / `AZURE_TENANT_ID` / `AZURE_SUBSCRIPTION_ID`
  variables — never a client secret — and run the signing step on a runner
  with a network path to the vault (in-VNet self-hosted runner or a
  Container Apps job), which does not exist yet.
- Set `REGISTRY_SIGNING_KEY_PATH` / `REGISTRY_SIGNING_PUBLIC_KEY_PATH` in
  that pipeline. If option (a) above is chosen, this is a config swap; if
  the algorithm changes, `signing.py`/`verify_signature.py` need the
  matching code change first.
- Delete `services/registry/keys/dev-signing-key.priv` from the working tree
  once no pipeline depends on it, and treat any remaining
  `WARNING: ... dev-signing-key` line in a production run as a build failure.

This follow-up is deliberately deferred out of this build's scope; it is
recorded here so it is not forgotten before any production rollout.

## Risk: Application Insights is a new cost-bearing resource

- **Component**: Application Insights (`infra/modules/console/app-insights.bicep`),
  a workspace-based resource linked to the existing `log-cmos-dev` Log
  Analytics workspace.
- **Decision**: Session `s7-console` introduces a new, workspace-based
  Application Insights resource in `southafricanorth`, deployed via
  `infra/modules/console/app-insights.bicep` and referenced from
  `infra/main.bicep`'s insertion point.
- **Decided by**: session s7-console build, per `.loop/spec.json`
  `INFRA-004` (C-1).
- **Reason**: `telemetry-lib`'s OpenTelemetry span export
  (`function_id`/`task_ref`/`model`/`registry_version`/`cost` tags) and
  the console's trace-timeline screen both require a real Application
  Insights ingestion endpoint; none existed in `cmos-dev` before this
  session (confirmed live: `az monitor app-insights component show`
  returned an empty list).

### Compensating controls

1. **Region pinned to `southafricanorth`** — `app-insights.bicep`'s
   `location` parameter is the literal string `'southafricanorth'`,
   never `resourceGroup().location`, so this guarantee cannot silently
   drift if another module's default changes. Per Microsoft Learn's
   Application Insights FAQ (verified this session): data is stored in
   the region the resource was created in **only if the region-specific
   connection string is used** — `telemetry-lib`'s
   `configure_tracer_provider` reads the connection string from this
   resource's own output (never a hardcoded global endpoint) and raises
   loudly if unset, rather than silently falling back.
2. **Workspace-based, not classic** — linked to the already-existing
   `log-cmos-dev` workspace (created in
   `container-apps-environment.bicep`), not a second, disconnected
   Log Analytics resource — no new data-residency surface beyond what
   `log-cmos-dev` already represents.
3. **Managed-identity query access** — the console's
   `AppInsightsClient` uses `azure-monitor-query`'s `LogsQueryClient`
   with `DefaultAzureCredential` (no connection-string round trip for
   reads), matching this repo's managed-identity-first posture.

### POPIA s72 cross-border-transfer nuance — NOT resolved by regional hosting alone

Per this session's domain-expert finding (`C-3`, `.loop/domain.md`):
whether hosting telemetry data in `southafricanorth` is, by itself,
sufficient to avoid a POPIA **s72** cross-border-transfer analysis (given
Microsoft is a US-headquartered operator that may access South-Africa-region
data from outside SA under standard support terms) is **not resolved** by
this build. This is recorded here as a **compensating-control-not-full-sign-off**
posture — matching the Service Bus entry above's pattern — not a closed
POPIA compliance question. Final s72 cross-border-transfer legal sign-off
for Application Insights/Log Analytics data remains an open item for
human/legal review (see `.loop/spec.json` `out_of_scope`/`open_questions`).

## Risk: console Easy Auth authenticates but does not yet authorize by operator

- **Component**: the console's Entra ID authConfig
  (`infra/modules/console/console-app.bicep`).
- **Decision**: as shipped, `consoleAuth`'s
  `validation.defaultAuthorizationPolicy.allowedApplications` is empty and
  no app-role/group claim is required — any user who can obtain a token
  for the console's App Registration in tenant
  `012ad0f2-8372-4425-82e4-c5e25967c3c9` passes Easy Auth, regardless of
  whether they are a designated console operator. This is an
  **authorization** gap, not an authentication one: unauthenticated
  requests are still correctly rejected (`AUTH-002`), but *any*
  authenticated tenant user currently reaches the kill-switch toggle, cost
  ledger, and Vault search.
- **Decided by**: flagged by risk-security review of build v2 (this
  session); not previously surfaced to or approved by the budget owner —
  recorded here explicitly rather than left undocumented.
- **Reason**: closing this fully (app-role or security-group claim
  enforcement in both the Bicep `authConfig` and `require_principal`)
  is a small but real scope addition beyond this session's frozen spec
  (`.loop/spec.json` v5), which only requires "authenticated", not
  "authorized by role".

### Compensating controls

1. **Manual sign-in restriction, documented as a required Phase 2 step**
   — `docs/console-auth-runbook.md`'s bootstrap runbook now instructs the
   human completing Phase 2 to set the App Registration's Enterprise
   Application **"Assignment required" = Yes** and assign only intended
   console operators (or a security group) before the console is
   considered production-ready — a Portal-only action with no Bicep/code
   change needed, closing the gap without touching the app.
2. **Audit trail still records operator identity** — every kill-switch
   toggle is still recorded against the real Easy-Auth principal
   (`console/app/services.py`'s `toggle_kill_switch`), so even before
   Phase 2's assignment restriction is applied, any access is
   individually attributable, not anonymous.

### Production hardening path

Before this console is relied on for real governance decisions at scale,
wire an explicit app-role or group-claim requirement into both
`consoleAuth`'s `validation` block and a matching check in
`console/app/auth.py`'s `require_principal`, so authorization is enforced
in code (defense-in-depth) rather than by Portal configuration alone —
mirroring the same code-level backstop pattern already used for
authentication (`RISK-003`).

## Retrieving Container Apps Job output (caj-vault-migrate / caj-vault-query)

- **Execution status** (stable CLI, no extension required):
  `az containerapp job execution list -g cmos-dev -n <job-name>`
- **Log content** (requires the `containerapp` CLI extension, bootstrapped
  non-interactively in the `preflight` job of `deploy-infra.yml`):
  `az containerapp job logs show -g cmos-dev -n <job-name>`

## Retrieving orchestrator Container App logs / job execution status

The orchestrator service (`services/orchestrator/`, session/s3-orchestrator)
follows the exact same agent-native retrieval pattern as the Vault
service above — no human dashboard required.

- **Container App logs** (`ca-orchestrator`):
  `az containerapp logs show -g cmos-dev -n ca-orchestrator`
- **Schema migration job execution status** (`caj-orchestrator-migrate`):
  `az containerapp job execution list -g cmos-dev -n caj-orchestrator-migrate`
- **Schema migration job logs**:
  `az containerapp job logs show -g cmos-dev -n caj-orchestrator-migrate`
- **Live smoke test job execution status** (`caj-orchestrator-smoke-test`,
  AC-028):
  `az containerapp job execution list -g cmos-dev -n caj-orchestrator-smoke-test`
- **Live smoke test job logs**:
  `az containerapp job logs show -g cmos-dev -n caj-orchestrator-smoke-test`

### Service Bus data-plane RBAC (AC-024) — applied automatically, not a manual step

Unlike the unresolved Key Vault RBAC gap documented for the Vault service
(see the compound learning on RBAC-mode Key Vault granting no data-plane
access by default), the orchestrator's Service Bus data-plane role
assignments are **not** left for a human operator to apply after the
fact. `infra/modules/orchestrator/container-app.bicep`,
`infra/modules/orchestrator/smoke-test-job.bicep`, and
`infra/modules/scheduling/*.bicep` each declare their own
`Microsoft.Authorization/roleAssignments` resources (granting "Azure
Service Bus Data Sender" and, for the orchestrator Container App only,
"Azure Service Bus Data Receiver" too) directly in Bicep. Because the
existing OIDC-authenticated deploy pipeline's identity holds **User
Access Administrator** on this subscription, these role assignments are
applied automatically as part of the normal `az deployment group create`
run in `deploy-infra.yml` — the same way the Vault service's Key Vault
Secrets User / AcrPull / Storage Blob Data Contributor role assignments
are applied today. No separate `az role assignment create` step or human
follow-up is required.

## CLOSED — Risk: the vault ran starlette 0.46.2 with seven known advisories

**Closed 2026-09-02.** Retained as a record; no acceptance is in force.

- **Component**: `services/vault` — its fastapi constraint, declared in both
  `services/vault/requirements.txt` and `services/vault/pyproject.toml`.
- **Was**: `fastapi>=0.111,<0.116`, resolving fastapi 0.115.14 → starlette
  0.46.2, which carried PYSEC-2026-161, -248, -249, -1941, -1942, -2280 and
  -2281. The vault was the only component with that stale cap; its siblings use
  `<0.141` or are uncapped and resolved to a clean starlette, which is why
  exactly one of the sixteen requirements files failed the audit.
- **Now**: `fastapi>=0.111,<0.141` in both files, resolving fastapi 0.140.13 →
  starlette 1.6.0 — above every fix version required. `pip-audit` reports "No
  known vulnerabilities found" for `services/vault/requirements.txt` with **no**
  ignore flags, so the baseline that briefly held these seven ids has been
  removed from `.github/pip-audit-ignore.txt` rather than left to accept
  advisories that no longer apply.

### How the bump was verified

The reason this was baselined rather than fixed immediately was that the vault
had no Python test job in CI — only the two migration jobs, which exercise
schema SQL rather than the service. That gap is now closed by the
`vault-tests` job in `.github/workflows/ci.yml`, and the bump was measured
against it:

- Against a real Postgres with both schemas applied and the service running,
  the suite reports **89 passed** on the old pins and **89 passed** on the new
  ones — the same tests, the same count.
- The five tests failing in both runs are the blob-storage ones
  (`test_asset_*`, `test_retention_expiry_deletes_expired_objects`, and the
  `assets` parametrisation of `test_create_and_roundtrip_taxonomy`). They need
  a real Azure Storage account, are unaffected by the bump, and are deselected
  in CI for that reason — see the job's own comment.
- The OpenAPI surface is byte-identical across the bump: the same 23 paths,
  none added, none removed.
- `len(app.routes)` drops from 51 to 17 across the bump while the OpenAPI count
  holds at 23 — an internal route-table representation change, not lost
  endpoints. Nothing in this repository introspects `app.routes`.
- One new warning, test-only and not a failure: fastapi's `TestClient` now
  emits `StarletteDeprecationWarning: Using httpx with starlette.testclient is
  deprecated; install httpx2 instead`. It affects
  `tests/test_telemetry_wiring.py` only.
