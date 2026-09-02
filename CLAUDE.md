# Canvas Marketing OS — project instructions

Agent-native marketing operations platform on Azure Container Apps, Postgres
Flexible Server, Service Bus and Key Vault, all VNet-integrated. Python 3.12.

Read `docs/architecture/README.md` for the full architecture set. Read
`.compound/index.md` for the accepted-learnings register — it is the record of
what has already gone wrong here, and it is normative, not trivia.

## Layout

| Path | What lives there |
|---|---|
| `contracts/` | Frozen, versioned interface contracts (OpenAPI, JSON Schema, DDL). Source of truth other components are validated against in CI. |
| `services/` | Long-running FastAPI services, one directory each. `telemetry-lib` is a shared sibling package, not a service. |
| `functions/` | Triggered compute, one directory per function app. `_shared/` holds cross-function config such as `scan-profiles.yaml`. |
| `infra/` | Bicep. `infra/modules/*.bicep` plus the `infra/main.bicep` orchestrator and environment parameter files. |
| `mcp/` | MCP servers (`mcp-web`, `mcp-buffer`, `mcp-canva`) and shared `common/`. |
| `console/` | Operator console: approvals, gate decisions, campaign review. |
| `scripts/` | Repo-local validators invoked by CI. |
| `.compound/` | Accepted learnings, one file per learning plus `index.md`. |

## Running the checks locally

```bash
ruff check services functions scripts console      # the CI lint job, verbatim
python scripts/validate_contracts.py               # contract correctness + frozen-v1 guard
bash scripts/validate_bicep.sh                     # compile infra + hold the warning ratchet
python scripts/check_allowlist_sync.py             # scan profiles vs. MCP_WEB_ALLOWLIST
python scripts/verify_governance_bundle_reconstruction.py --self-test
```

Service test suites are per-directory and install their own dependencies:
`pip install -r services/<svc>/requirements.txt -r services/<svc>/requirements-test.txt`
then `cd services/<svc> && pytest -q`. Several suites need a live Postgres with
the frozen schemas applied — see the matching job in `.github/workflows/ci.yml`
for the exact ordering rather than guessing.

## Hard rules

These are the ones that have already cost a live incident here. Breaking one is
never a nit.

1. **Frozen contracts.** Anything under `contracts/` is frozen at v1. Changes
   must be additive; a breaking change lands under a new version namespace
   (`/v2/`), never by mutating the published file. `contracts/.frozen-v1.sha256`
   is the baseline guard.
2. **Governance bundle.** A new source file in `services/gatekeeper/` or
   `services/publisher/` must be added to `BUNDLE_MANIFEST.txt` *and* to
   `infra/main.bicep`'s `loadTextContent` entries in the same change. Missing
   either produces a crash-looping revision inside the VNet, not a CI failure.
3. **Allow-list sync.** Hosts added to `functions/_shared/scan-profiles.yaml`
   must also be added to `MCP_WEB_ALLOWLIST` in `infra/main.bicep`. A profile
   host missing from the Bicep degrades the scan silently.
4. **Never resolve an Azure resource from an ambient list.** No
   `az <thing> list --query "[0].x"` — it picks the wrong resource the moment a
   second one exists (L-0021). Resolve from the shared resource's own IaC
   deployment record (`az deployment group show -n <module>`), not from a
   consumer's binding when that consumer is itself being migrated (L-0036).
   Same rule for hostnames: read the live `ingress.fqdn`, never a contract's
   aspirational `servers:` URL (L-0025).
5. **Bicep ordering is a real reference.** Cross-resource ordering must use
   `module.outputs.x`, never a separately-computed string that happens to match
   (L-0017).
6. **New Container App or Job fed by CI-built images** carries the whole
   bootstrap contract at design time, not as follow-up: image param defaults to
   a public placeholder, the deploy preflight preserves the live image once one
   exists, and the image workflow resolves its registry from an authoritative
   deployment record (L-0048, L-0060). A `Microsoft.App/jobs` resource takes no
   `identity` block at all in its initial create (L-0061).
7. **Runtime reads of repo-root contract files** use model-gateway's
   `CONTRACTS_DIR` pattern — env override, checkout-relative fallback,
   Dockerfile staging COPY, image-workflow staging step — applied to every call
   site at once (L-0062).
8. **Telemetry imports.** `from telemetry_lib import ...` must be guarded or the
   image must provably contain the package. "Passes CI but the deployed image
   lacks `telemetry_lib`" has recurred four times (L-0066).
9. **Secrets.** Never generate a URI-embedded secret with
   `openssl rand -base64` — `/+=` breaks URI parsing (L-0004). A committed
   dev/test key needs both a static warning and a runtime `WARNING` on every
   fallback use (L-0041).
10. **A shared-mechanism fix is a bug-class fix.** Patching one call site of a
    shared helper or template reliably leaves siblings broken — audit every call
    site and re-run the full suite with a before/after pass count (L-0013,
    L-0075).

## Conventions

- Work happens on `session/{id}` branches in per-session `git worktree`s. Many
  sibling sessions are unmerged at any time; probe `git branch -a` and
  `git log main..<branch>` before building something a sibling may already have
  (L-0042, L-0055).
- Verify commands are code and can be wrong in both directions: they can pass
  vacuously (L-0005, L-0046, L-0059) and fail spuriously on checkout or platform
  artifacts (L-0044, L-0058). Prove a new check can both pass and fail before
  trusting it.
- A CI step that pipes `az ... --query` must echo the raw unfiltered output
  first, so a failure shows ground truth rather than an already-filtered blank
  (L-0064).
- New accepted learnings append a one-line entry to `.compound/index.md` and a
  file under `.compound/learnings/<class>/`, class being one of `conventions`,
  `architecture`, `security`, `known-hard`.
- An external identifier — a model id, a hostname, a vendor field name — is a
  hypothesis until a live call returns it. Mocked providers accept any string
  (L-0026, L-0068, L-0078).

## Continuous review

This repository is reviewed by an independent Claude reviewer that cannot push:
a per-PR review and a weekly rotating system audit. See
`docs/continuous-review.md`. Review-only guidance lives in `REVIEW.md`.
