# Review instructions

This repository is written almost entirely by parallel agent sessions. That
changes what a review is for: the author had no second pair of eyes, the diff
was verified by machine-written verify commands that can themselves be wrong,
and plausible-sounding prose in a commit message is not evidence. Read the code.

## What Important (🔴) means here

Reserve 🔴 for findings that would break production, corrupt governance, leak
data, or ship a silently-degraded system:

- A violation of any numbered hard rule in `CLAUDE.md`. All ten are 🔴, not
  nits — each one has already caused a live incident in this repository.
- A change to anything under `contracts/` that is not strictly additive.
- Governance and gate logic: a bypass, a policy entry granting elevated access
  to a test/smoke/debug `function_id`, an approval path a smoke test can shortcut.
- Data handling: PII or client content in logs, error messages, or telemetry;
  raw request content reaching a client-facing message.
- A failure mode that degrades quietly rather than failing loudly — a swallowed
  exception, a per-source `try/except` that turns a broken dependency into a
  short result, a fixture returned where live data was intended.
- A migration that is not idempotent, or not backward compatible with the
  revision currently running.

Style, naming, structure and refactoring suggestions are 🟡 Nit at most.

## Always check

- **The deployed shape, not the local one.** Path code that assumes a deep repo
  checkout, logging configured at import time, a package present locally but
  absent from the image — these pass every local test and fail at the container
  entrypoint (L-0014, L-0066). If a change adds an import or a file read that
  runs inside a container, say which Dockerfile stages it.
- **Every call site of a changed shared helper.** If the diff edits a shared
  mechanism and touches fewer call sites than exist, that is a 🔴 finding with
  the unpatched siblings named (L-0013, L-0075).
- **New or changed verify/CI assertions.** Can the check actually fail? A
  `grep -v` scope guard exits 1 on its passing case; a substring-absence assert
  can be un-failable by construction; a diff-against-HEAD criterion passes
  vacuously on an empty range (L-0046, L-0058, L-0059). State which direction
  you tested.
- **Config declared in two places.** Anything set on a live Azure resource but
  absent from `infra/` is config drift that the next deploy reverts. Flag it
  even when the code is correct.
- **New Container Apps, Jobs and workflows** against hard rules 6 and 7, and
  against the workflow it claims to copy — a header comment saying "copied
  deploy-X.yml" is a claim, and one dropped step has silently broken an entire
  workflow's history here (L-0073).
- **External identifiers** introduced or edited: model ids, hostnames, vendor
  field names, tool names. Say explicitly whether anything in the diff could
  have proven the value is real, or whether only a mock ever sees it (L-0026).

## Do not report

- Anything CI already enforces: `ruff` findings, formatting, Bicep compile
  errors, contract schema validity, allow-list sync, loop schema validity.
- Generated files, lockfiles, `docs/architecture/_pdf-build/` output, and
  `docs/architecture/Canvas-Marketing-OS-Architecture.pdf`.
- Missing type annotations, docstring style, and import grouping.
- Test code that deliberately violates a production rule, where the test says so.
- Known items already in `docs/architecture/09-technical-debt.md` or
  `docs/accepted-risks.md` unless this diff makes one materially worse — in that
  case cite the existing TD id rather than re-describing it.

## Verification bar

State the evidence, not the inference.

- A behaviour claim needs a `file:line` citation in the source. Naming, a
  docstring, or a commit message is not evidence of behaviour.
- A "this reuses existing code" or "this matches the existing pattern" claim in
  the diff must be checked against the named file before you accept it. That
  claim has been factually false here (L-0067).
- If you cannot establish a finding from the code in under a few minutes,
  downgrade it to a question in the summary rather than posting it as a finding.

## Volume and convergence

- At most five 🟡 Nits per review. If there are more, report the count in the
  summary instead of posting them inline.
- After the first review of a PR, post 🔴 findings only. A one-line fix must not
  reach round seven on style.
- Open the summary with a one-line tally — `2 important, 4 nits` — and lead with
  "no blocking issues" when that is the case.
