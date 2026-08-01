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
- **Used by**: campaign execution / social publishing.
- **Cross-border transfer note**: Buffer is a foreign-hosted (US) social
  scheduling platform. A **POPIA** s72 cross-border transfer ground or DPA
  must be established before real client/campaign personal data flows to
  Buffer; not resolved by this build, flagged here only.

## 3. Canva

- **Key Vault secret names**: `canva-client-id`, `canva-client-secret`
  (both populated and used by mcp-canva's dual-mode gate — see
  `mcp/mcp-canva/app/dispatch.py`). `canva-refresh-token` is **pending,
  not yet populated** — mcp-canva's OAuth2+PKCE consent flow
  (`mcp/mcp-canva/scripts/oauth_consent.py`) has not been run against the
  live Canva app yet, so no refresh token has been minted or stored in
  Key Vault. Until it is, mcp-canva runs in fixture mode for any call
  that would require a live access token.
- **Used by**: asset generation / design automation.
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
  vault-wide as a documented first-run bootstrap exception — see
  `infra/modules/vault/secret-writer-job.bicep`'s header comment) — no
  client secret, no plaintext password ever leaves the VNet or enters
  deployment history.
- **Cross-border transfer note**: not applicable — `psql-cmos-dev` is an
  in-region (`southafricanorth`) Azure Database for PostgreSQL server;
  no cross-border transfer occurs for this credential.

---

**Scope note**: for entries 1-8 above, this document only names the
required secret and flags the cross-border transfer consideration per
integration — populating those secrets' actual values, executing DPAs,
and performing a Section 72 transfer-impact assessment are explicitly
out of scope for this build (see `.loop/spec.json` `out_of_scope`).
Entry 9 (`vault-db-connection-string`) is the one exception: its value
*is* populated by this build, exclusively through the in-VNet
`caj-vault-secret-writer` job described above — never a human-run
`az keyvault secret set` from outside the VNet.
