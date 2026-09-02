---
id: 01-security-and-data
title: Security, secrets and data handling
---

# Lens: security, secrets and data handling

## In scope

- Secret lifecycle: generation, storage, injection, rotation. Every value that
  reaches a container as an env var or a Key Vault reference — where does it come
  from, who can read it, and what happens when it rotates.
- Committed credentials, dev/test keys and fallbacks. Hard rule 9 in `CLAUDE.md`
  requires a committed dev key to carry both a static warning and a runtime
  `WARNING` on every fallback use. Check both halves, at every fallback.
- The permission model: `docs/permission-register.yaml`,
  `docs/architecture/14-security-and-permission-model.md`, RBAC grants in
  `infra/`, and Container Apps auth config. Look for a grant wider than the
  consumer needs, and for a resource whose data-plane access nobody actually has.
- Policy config with a standing bypass — grep policy files for `test`, `smoke`,
  `debug` and `dev` `function_id`s granting elevated access (L-0029).
- PII and client content in logs, telemetry, error messages and dead-letter
  payloads. POPIA obligations are real here, not decorative.
- Network posture: public network access, private endpoints, allow-lists, and
  anything reachable that the architecture docs describe as internal-only.
- Inputs that cross a trust boundary: `mcp-web` fetches, Buffer and Canva
  responses, webhook payloads, and anything a scanned page can put into a prompt.

## Out of scope for this lens

Dependency CVEs (CodeQL and the dependency audit own those), IaC drift that is
not security-relevant, and cost.

## Specific things this repository has got wrong before

- `openssl rand -base64` for a URI-embedded secret (L-0004).
- RBAC-mode Key Vault granting no data-plane access to anyone, including owners
  (L-0011).
- `publicNetworkAccess=Disabled` silently ignoring IP firewall rules (L-0012).
- A smoke test's need for "something to gate-check" leaking into the real policy
  config as a standing bypass (L-0029).
- A dual-mode "goes live when credentials exist" design starting real vendor
  calls the moment an unrelated session added a secret (L-0074).
