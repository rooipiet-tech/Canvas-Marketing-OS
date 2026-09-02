# Credentials runbook — Wave 1 external integrations

This runbook lists every Wave-1 external integration, the Key Vault secret
name that will hold its credential (documentation only — **no secret
values are populated by this build**, see `.loop/spec.json` out_of_scope),
and the cross-border data transfer consideration that applies before any
real (non-test) data is sent to that provider.

All secret names conform to Key Vault naming rules: they start with a
letter and contain only letters, digits, and hyphens.

## 1. Anthropic

- **Key Vault secret name**: `anthropic-api-key`
- **Used by**: model-gateway service (LLM completions).
- **Cross-border transfer note**: Anthropic is a foreign-hosted (US)
  processor. Under **POPIA s72**, a cross-border transfer of personal
  information to a foreign processor requires an adequate transfer ground
  (e.g. the data subject's consent, or a Data Processing Agreement with
  equivalent safeguards) to exist before any real personal/client data is
  sent to this provider. This is documentation only — legal sign-off is
  out of scope for this build.

## 2. Buffer

- **Key Vault secret name**: `buffer-api-key`
- **Used by**: campaign execution / social publishing; also
  analytics-ingest's direct Buffer GraphQL client
  (`services/analytics-ingest/analytics_ingest/buffer_client.py`) for
  nightly post-performance metrics ingestion — see the analytics entries
  below for the session/s9-analytics analytics connectors. Buffer is the
  one live-capable analytics source this session (a gated one-shot
  introspection smoke test, `caj-analytics-buffer-smoke`, verifies the
  live GraphQL schema before any live nightly run — L-0026/L-0064).
- **Cross-border transfer note**: Buffer is a foreign-hosted (US) social
  scheduling platform. A **POPIA** s72 cross-border transfer ground or DPA
  must be established before real client/campaign personal data flows to
  Buffer; not resolved by this build, flagged here only.

## 3. Canva

- **Key Vault secret names**: `canva-client-id`, `canva-client-secret`
  (both populated). `canva-refresh-token` is **pending, not yet
  populated** — mcp-canva's OAuth2+PKCE consent flow
  (`mcp/mcp-canva/scripts/oauth_consent.py`) has not been run against the
  live Canva app yet, so no refresh token has been minted or stored in
  Key Vault. Until it is, mcp-canva runs in fixture mode for any call
  that would require a live access token.
- **A3, 2 Sep 2026 — that last sentence is now TRUE.** It was the
  documented intent all along, but not the behaviour: mcp-canva's gate
  asked only whether `canva-client-id` and `canva-client-secret` were
  present, and both are wired into the deployed Container App, so it read
  as live and issued every call with the literal header `Authorization:
  Bearer None`. The gate now requires a usable access token, and the
  module exchanges `canva-refresh-token` for one itself — nothing
  performed that exchange before, so even a correctly-populated secret
  would not have produced a single working call.
- **To turn Canva on**, in order:
  1. Run `python mcp/mcp-canva/scripts/oauth_consent.py --client-id …
     --client-secret …` and complete the browser consent. It requests the
     brand-template scopes (`brandtemplate:content:read`,
     `brandtemplate:meta:read`) alongside the design ones — autofill needs
     them for the dataset lookup, and the old two-scope default produced a
     token that 403'd on the call it depends on.
  2. Load the printed refresh token into Key Vault as
     `canva-refresh-token` via the gated in-VNet path (L-0012). Restart
     ca-mcp-canva so it picks up the new secret. mcp-canva flips from
     fixture to live on its own at that point.

     This step only works because ca-mcp-canva now carries `KEY_VAULT_URI`
     and `AZURE_CLIENT_ID` (infra/main.bicep's `mcpCanvaApp`). Its
     `envVars` was empty, so `resolve_secret` had neither of its two
     routes to the secret — not the env var, not the SDK lookup — and the
     restart in this step would have changed nothing: `_live_mode()` would
     have stayed `False` and every carousel run would have returned a
     fixture, silently, with no error and no 401. `id-mcp-canva` held Key
     Vault Secrets User the whole time; that grant is inert without both
     variables.

     A Container Apps `secretRef` was the wrong shape for this one secret:
     a reference to a Key Vault secret that does not exist yet fails the
     revision, so adding one would have broken every deploy until somebody
     minted a token.
  3. Set `canvaDryRun` to `'false'` in
     `infra/modules/orchestrator/container-app.bicep` when you want
     Wednesday's carousel to actually generate a deck. It is declared
     explicitly at `'true'` today rather than left to the code default.
- **Known gap after all three steps**: text slides will autofill, images
  will not. Canva fills an image field by asset id, and function 45's
  manifest carries a filename because nothing uploads carousel imagery to
  Canva yet. mcp-canva skips those fields and names them in its result
  rather than failing the whole job. Closing it needs an asset-upload
  step; the consent flow already requests `asset:write` so that will not
  need a second trip through it.
- **Rotation caveat**: Canva rotates refresh tokens on use and mcp-canva
  cannot write to Key Vault, so the rotated token lives in-process only.
  After a long idle period plus a restart, live calls may fail with
  `invalid_grant` — re-run the consent flow and reload the secret.
- **Used by**: asset generation / design automation. Called by the
  orchestrator's `draft_carousel_post_handler` (function 45's Bulk Create
  manifest → `bulk_create_from_csv`) since A3.
- **Cross-border transfer note**: Canva is a foreign-hosted (Australia/US)
  design platform. Per **POPIA s72**, a cross-border transfer ground or
  Data Processing Agreement must exist before real personal information
  is included in any design asset sent to Canva.

## 4. Semrush

- **Key Vault secret name**: `semrush-api-key`
- **Used by**: signal ingestion (SEO/competitive research).
- **Cross-border transfer note**: Semrush is a foreign-hosted (US)
  analytics provider. A **POPIA** s72 cross-border transfer ground or DPA
  must be confirmed before any real personal information is submitted in
  Semrush queries or reports.

## 5. Fireflies

- **Key Vault secret name**: `fireflies-api-key`
- **Used by**: meeting-transcript signal ingestion.
- **Cross-border transfer note**: Fireflies is a foreign-hosted (US)
  transcription/meeting-intelligence provider, and transcripts routinely
  contain personal information. Under **POPIA s72**, a cross-border
  transfer ground or DPA must exist before real meeting data (which will
  contain personal information) is sent for transcription.

## 6. LinkedIn developer app

- **Key Vault secret name**: `linkedin-client-secret`
- **Used by**: campaign execution / social publishing (LinkedIn).
- **Cross-border transfer note**: LinkedIn is a foreign-hosted (US/EU)
  platform. A **POPIA** s72 cross-border transfer ground or DPA must be
  established before real personal data (e.g. audience/contact data) is
  processed via the LinkedIn developer app.

## 7. Google Analytics / Search Console

- **Key Vault secret name**: `google-oauth-client-secret`
- **Used by**: signal ingestion (web analytics, search performance).
- **Cross-border transfer note**: Google is a foreign-hosted (US)
  provider. Analytics data can include personal information (e.g.
  identifiers tied to individuals); a **POPIA** s72 cross-border transfer
  ground or DPA must exist before real analytics data is exported or
  queried via this integration.

## 8. Microsoft Graph / Teams webhook

- **Key Vault secret name**: `microsoft-graph-client-secret`. The
  Gatekeeper's Adaptive Card notification path additionally reads a
  `teams-webhook-url` secret (`services/gatekeeper/app/config.py`'s
  `teams_webhook_url()`) — this secret is **pending, not yet populated**
  in Key Vault today. With it absent, `ca-gatekeeper` falls back to the
  approval-inbox/console surface
  (`services/gatekeeper/app/approval_inbox.py`) so approval cards still
  land observably; no Teams webhook POST is attempted while the secret
  is unset.
- **Used by**: internal notifications / gate-decision alerts (Teams).
- **Cross-border transfer note**: Microsoft Graph/Teams may route through
  foreign-hosted (non-South African) regions depending on tenant
  configuration. A **POPIA** s72 cross-border transfer ground or DPA
  (or confirmation of in-region data residency) must be established
  before real personal data appears in Teams notifications.

## 9. Vault service — Postgres connection string

- **Key Vault secret name**: `vault-db-connection-string`
- **Used by**: the Vault service (`ca-vault`, `infra/modules/vault/container-app.bicep`) —
  reads this secret at runtime via its managed identity (Key Vault
  Secrets User role, `AC-010` / `L-0011`) to connect to `psql-cmos-dev`.
- **Loading procedure**: unlike the other 8 entries above, this secret's
  *value* is populated by this build (it is not out of scope) — but it is
  loaded exclusively through an in-VNet Container Apps Job
  (`caj-vault-secret-writer`, `infra/modules/vault/secret-writer-job.bicep`),
  never by temporarily flipping Key Vault's `publicNetworkAccess` to
  `Enabled` (`L-0012`). Start it with:

  ```
  az containerapp job start -g cmos-dev -n caj-vault-secret-writer
  ```

  Poll to completion and inspect logs the same way as
  `caj-vault-migrate`/`caj-vault-query` (see "Retrieving Container Apps
  Job output" in `docs/accepted-risks.md`):

  ```
  az containerapp job execution list -g cmos-dev -n caj-vault-secret-writer
  az containerapp job logs show -g cmos-dev -n caj-vault-secret-writer --execution <execution-name>
  ```

  The job builds the connection string from the same
  `administratorLoginPassword` secure parameter threaded to
  `caj-vault-migrate`/`caj-vault-query`, and writes it to Key Vault using
  its own system-assigned managed identity (Key Vault Secrets Officer,
  vault-wide as a first-run bootstrap necessity — the job creates the
  secret, so on a first run there is nothing narrower to scope to; see
  `infra/modules/vault/secret-writer-job.bicep`'s header comment) — no
  client secret, no plaintext password ever leaves the VNet or enters
  deployment history.
- **Retiring the bootstrap-wide grant** (finding 1 of the
  `01-security-and-data` audit, issue #135). Once the job has run and
  `vault-db-connection-string` exists, the vault-wide grant is no longer
  needed and should be narrowed to that one secret. Set
  `narrowScopeToDbSecret: true` on the `secretWriterJob` module in
  `infra/modules/vault/main.bicep` and deploy.

  Do **not** narrow it with `az role assignment` instead. This module is
  part of the whole-platform template, so the next `deploy-infra` run
  re-applies the vault-wide assignment as declared and silently undoes the
  change — L-0065. The parameter is the only form of the narrowing that
  survives a redeploy.

  Confirm the secret exists before flipping it; with the parameter true, a
  deploy against an environment where the job has never run has no secret
  resource to scope the assignment to:

  ```bash
  az keyvault secret show --vault-name "$KV" --name vault-db-connection-string \
    --query 'attributes.enabled' -o tsv
  ```

  After deploying, the job's identity should hold exactly one assignment,
  at the secret scope:

  ```bash
  az role assignment list --assignee "$JOB_PRINCIPAL_ID" --all \
    --query "[].{role:roleDefinitionName, scope:scope}" -o table
  ```
- **Cross-border transfer note**: not applicable — `psql-cmos-dev` is an
  in-region (`southafricanorth`) Azure Database for PostgreSQL server;
  no cross-border transfer occurs for this credential.

## 10. LinkedIn Community Management API (analytics)

- **Key Vault secret name**: `linkedin-analytics-client-secret`
- **Used by**: analytics-ingest's LinkedIn connector
  (`services/analytics-ingest/analytics_ingest/linkedin_client.py`) —
  nightly post-performance metrics ingestion via
  `caj-analytics-nightly-ingest`. Distinct from entry 6's
  `linkedin-client-secret`, which is scoped to campaign execution/social
  publishing, not analytics — the LinkedIn Community Management API
  requires its own app registration and credential.
- **Cross-border transfer note**: LinkedIn is a foreign-hosted (US/EU)
  platform. Under **POPIA s72**, a cross-border transfer ground or DPA must be
  established before real analytics data (which can include personal
  information, e.g. identifiers tied to individuals) is exported or
  queried via the LinkedIn Community Management API. Fixture-first is
  mandatory for this build — no live LinkedIn Community Management API
  call is made (see `.loop/spec.json` `out_of_scope`).

## 11. GA4 property + service account

- **Key Vault secret name**: `ga4-service-account-key`
- **Used by**: analytics-ingest's GA4 connector
  (`services/analytics-ingest/analytics_ingest/ga4_client.py`) — nightly
  web analytics ingestion via `caj-analytics-nightly-ingest`. Distinct
  from entry 7's `google-oauth-client-secret`, which covers both GA4 and
  Search Console coarsely — this entry is the GA4-specific service
  account credential.
- **Cross-border transfer note**: Google is a foreign-hosted (US)
  provider. Analytics data can include personal information (e.g.
  identifiers tied to individuals); under **POPIA s72**, a cross-border transfer
  ground or DPA must exist before real GA4 data is exported or queried.
  Fixture-first is mandatory for this build — no live GA4 Data API call
  is made (see `.loop/spec.json` `out_of_scope`).

## 12. Search Console API + verified-site service account

- **Key Vault secret name**: `search-console-service-account-key`
- **Used by**: analytics-ingest's Search Console connector
  (`services/analytics-ingest/analytics_ingest/search_console_client.py`)
  — nightly search performance ingestion via
  `caj-analytics-nightly-ingest`. Distinct from entry 7's
  `google-oauth-client-secret` — this entry is the Search Console-specific
  verified-site service account credential.
- **Cross-border transfer note**: Google is a foreign-hosted (US)
  provider. Search Console data can include personal information (e.g.
  identifiers tied to individuals); under **POPIA s72**, a cross-border transfer
  ground or DPA must exist before real Search Console data is exported or
  queried. Fixture-first is mandatory for this build — no live Search
  Console API call is made (see `.loop/spec.json` `out_of_scope`).

---

**Scope note**: for entries 1-8 and 10-12 above, this document only names
the required secret and flags the cross-border transfer consideration per
integration — populating those secrets' actual values, executing DPAs,
and performing a Section 72 transfer-impact assessment are explicitly
out of scope for this build (see `.loop/spec.json` `out_of_scope`).
Entries 10-12 (LinkedIn Community Management API, GA4, Search Console)
were added by session/s9-analytics alongside a correction to entry 2's
previously-stale Buffer secret name (now `buffer-api-key`, matching the
name used everywhere else in the repo); all three new entries are
fixture-first this session — no live credential is populated and no live
external call is made. Entry 9 (`vault-db-connection-string`)
remains the one exception where a secret's value *is* populated by this
build, exclusively through the in-VNet `caj-vault-secret-writer` job
described above — never a human-run `az keyvault secret set` from outside
the VNet.
