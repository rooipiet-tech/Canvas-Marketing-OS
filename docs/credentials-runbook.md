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

- **Key Vault secret name**: `buffer-access-token`
- **Used by**: campaign execution / social publishing.
- **Cross-border transfer note**: Buffer is a foreign-hosted (US) social
  scheduling platform. A **POPIA** s72 cross-border transfer ground or DPA
  must be established before real client/campaign personal data flows to
  Buffer; not resolved by this build, flagged here only.

## 3. Canva

- **Key Vault secret name**: `canva-client-secret`
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

- **Key Vault secret name**: `microsoft-graph-client-secret`
- **Used by**: internal notifications / gate-decision alerts (Teams).
- **Cross-border transfer note**: Microsoft Graph/Teams may route through
  foreign-hosted (non-South African) regions depending on tenant
  configuration. A **POPIA** s72 cross-border transfer ground or DPA
  (or confirmation of in-region data residency) must be established
  before real personal data appears in Teams notifications.

---

**Scope note**: this document only names the required secret and flags
the cross-border transfer consideration per integration. Populating
actual secret values, executing DPAs, and performing a Section 72
transfer-impact assessment are explicitly out of scope for this build
(see `.loop/spec.json` `out_of_scope`).
