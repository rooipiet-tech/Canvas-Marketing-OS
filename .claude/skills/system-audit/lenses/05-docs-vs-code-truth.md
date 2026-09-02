---
id: 05-docs-vs-code-truth
title: Documentation versus code truth
---

# Lens: documentation versus code truth

`docs/architecture/` is an 18-document reference that operators, and future
agent sessions, treat as ground truth. When it drifts, the drift propagates into
specs and then into code (L-0038, L-0068, L-0078: a goal author's own factual
claims about a dependent service have been wrong here, and the code was built to
match the claim).

## In scope

- Claims in `docs/architecture/` that the code contradicts: a service that no
  longer exists, an endpoint that moved, a tool count, a field name, a data flow
  with a step that was removed. Cite both sides — the doc line and the code line.
- `docs/architecture/02-module-catalogue.md`, `11-api-catalogue.md`,
  `12-integration-catalogue.md` and `13-ai-agent-catalogue.md` against the actual
  contents of `services/`, `functions/`, `mcp/` and `contracts/`. New function
  apps and MCP tools are added often; catalogues are updated less often.
- `docs/architecture/09-technical-debt.md`: items already fixed and still listed,
  items whose severity has changed, and items whose "where" citation no longer
  resolves to the code described.
- Runbooks under `docs/` — `run-the-loop.md`, `credentials-runbook.md`,
  `console-auth-runbook.md` — against the commands and env vars that currently
  exist. A runbook step that fails is a production incident during an incident.
- `.compound/index.md` entries whose fix has since been reverted, and learnings
  marked `active` that the code no longer honours anywhere.
- `README.md` and per-directory READMEs against the layout.

## Out of scope for this lens

Prose style, formatting, and the generated PDF under `docs/architecture/`.

## How to report

Severity follows consequence, not size: a runbook that would strand an operator
mid-incident is S2; a stale module count is S4 and should usually be dropped
under the "S3 and above" cap. Prefer one finding listing N stale catalogue rows
over N findings.
