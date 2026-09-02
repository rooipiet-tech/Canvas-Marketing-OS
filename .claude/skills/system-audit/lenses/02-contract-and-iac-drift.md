---
id: 02-contract-and-iac-drift
title: Contract and infrastructure drift
---

# Lens: contract and infrastructure drift

The question this lens asks: **where does the running system differ from what
the repository says it is?**

## In scope

- Anything set on a live Azure resource that does not appear in `infra/`. The
  next `az deployment group create` reverts it, and ARM replaces `env` lists
  declaratively — so an undeclared env var is a scheduled outage, not a nit.
  TD-31 is the worked example; look for its siblings.
- Values written down in two places that nothing keeps in sync. The known pairs
  are `functions/_shared/scan-profiles.yaml` ↔ `MCP_WEB_ALLOWLIST`, and
  `BUNDLE_MANIFEST.txt` ↔ `main.bicep`'s `loadTextContent` entries — both have a
  CI check. Find the pairs that do not.
- Contracts under `contracts/` versus the code that claims to implement them:
  fields a service reads that the schema does not define, endpoints in the
  OpenAPI that no route serves, DDL columns no migration creates.
- A contract's `servers:` URL, a hardcoded FQDN or an env default that no DNS
  zone or custom domain backs (L-0025).
- Bicep ordering that relies on a computed string rather than a real
  `module.outputs` reference (L-0017), and resource resolution from an ambient
  `list --query "[0]"` (L-0021, L-0036).
- New Container Apps and Jobs against the image-bootstrap contract (L-0048,
  L-0060, L-0061), and runtime contract-file reads against the `CONTRACTS_DIR`
  pattern (L-0062).
- Sibling workflows that redeploy the same template and silently reset state an
  out-of-band step set (L-0065).

## Out of scope for this lens

Application logic bugs, test quality, cost.

## How to look

Start from `infra/main.bicep` and walk outward to each module's consumers, then
from each service's config reads back to where the value is declared. The
findings that matter are the ones where both sides look correct in isolation.
