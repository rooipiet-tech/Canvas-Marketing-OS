# Canvas Marketing OS

Agent-native marketing operations platform: signal ingestion, opportunity
scoring, brief generation, asset production, campaign execution, and cost/gate
governance — built on Azure Container Apps, Postgres Flexible Server, Service
Bus, and Key Vault, all VNet-integrated.

> **New here? Start with [`docs/architecture/`](docs/architecture/README.md)** —
> an 18-document architecture and product reference reverse-engineered from
> this source tree: system architecture, module catalogue, data model, AI
> architecture, business capability map, operating model, positioning,
> technical debt register, roadmap, API/integration/agent catalogues, security
> and permission model, deployment guide, and product strategy.

## Repo layout

| Path | Purpose |
|---|---|
| `contracts/` | Frozen, versioned interface contracts: OpenAPI specs, JSON Schemas, DDL, spec docs. The single source of truth other components are validated against in CI. |
| `services/` | Long-running HTTP services (FastAPI), one directory per service. |
| `functions/` | Event-driven / triggered compute (Azure Functions), one directory per function app. |
| `infra/` | Bicep infrastructure-as-code: `infra/modules/*.bicep` plus the `infra/main.bicep` orchestrator and environment parameter files. |
| `mcp/` | Model Context Protocol server/tool definitions for agent-native operation. |
| `console/` | Human-facing operator console (approvals, gate decisions, campaign review). |
| `docs/` | Runbooks, risk register, and other operational documentation. |
| `docs/architecture/` | Enterprise architecture and product documentation set (start at its `README.md`). |
| `scripts/` | Repo-local automation, e.g. `scripts/validate_contracts.py` used by CI. |

## Development workflow: worktree-per-session

This repository is built by a spec-driven agent loop that runs each unit of
work — a session — in its own `git worktree`, checked out to its own branch.
This keeps concurrent sessions isolated (no shared working directory, no
branch-switching races) and keeps `main` protected until a session's changes
are reviewed and merged.

### Branch naming: `session/{id}`

Every session branch is named `session/{id}`, where `{id}` is a short,
stable identifier for that unit of work (e.g. a milestone slug or ticket id).
This repository's own foundation build was done on the branch:

```
session/s0-foundation
```

i.e. session id `s0-foundation` — the first ("s0") session, scaffolding the
repo foundation (contracts, infra, CI/CD, docs).

### Creating a new session worktree

From a primary clone of this repository:

```bash
# Create a new branch off main for the session, in its own worktree directory
git worktree add ../cmos-session-<id> -b session/<id> main

cd ../cmos-session-<id>
# ... do the session's work, commit on session/<id> ...
```

When the session's work is reviewed and merged into `main`, remove the
worktree:

```bash
git worktree remove ../cmos-session-<id>
```

This convention is also used by CI's own verification pass (see
`.github/workflows/ci.yml`), which checks out a fresh worktree of `main` to
confirm a clean build stays green end to end.

## Contracts and governance

Interface contracts under `contracts/` are frozen once published (v1). Any
non-additive ("breaking") change to a frozen contract must land under a new
version namespace (e.g. `/v2/`) rather than mutating the existing one in
place. `scripts/validate_contracts.py` enforces this in CI — see
`contracts/.frozen-v1.sha256` for the baseline hash guard.

## Infrastructure

All Azure infrastructure is defined in `infra/` as Bicep and deployed via
`.github/workflows/deploy-infra.yml` using OIDC federated identity (no client
secrets). See `docs/accepted-risks.md` for the one explicitly accepted dev-only
risk (Service Bus Standard SKU, no private endpoint) and its compensating
controls.

## Credentials

External integration credentials are never committed. See
`docs/credentials-runbook.md` for the Key Vault secret naming convention and
the cross-border data transfer considerations (POPIA) that apply to each
foreign-hosted provider.
