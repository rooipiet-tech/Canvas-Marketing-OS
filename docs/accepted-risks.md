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

## Retrieving Container Apps Job output (caj-vault-migrate / caj-vault-query)

- **Execution status** (stable CLI, no extension required):
  `az containerapp job execution list -g cmos-dev -n <job-name>`
- **Log content** (requires the `containerapp` CLI extension, bootstrapped
  non-interactively in the `preflight` job of `deploy-infra.yml`):
  `az containerapp job logs show -g cmos-dev -n <job-name>`
