---
id: 04-verification-integrity
title: Test and verification integrity
---

# Lens: test and verification integrity

The question this lens asks: **which of our green checks cannot go red?**

This repository is machine-authored and machine-verified. A check that cannot
fail is worse than no check, because it is counted as coverage.

## In scope

- Assertions that are un-failable by construction: a substring-absence assert
  the encoding guarantees (L-0046), a `grep -v` scope guard that exits 1 on its
  passing case (L-0058), a diff-against-HEAD criterion over an empty range
  (L-0059), a `X && (...exit 1) || Y` idiom that always takes the `||` branch
  (L-0051).
- Mocks and stubs that accept anything: a routing table entry, a tool name, a
  vendor field that only ever meets a mock is unverified no matter how many
  tests pass (L-0026). Mock outputs that violate their own package's
  `schema.json` (L-0005).
- Tests that pass locally for reasons that will not hold in the container: deep
  checkout paths, `caplog`/`monkeypatch` fixtures standing in for process-level
  startup state, a package installed locally but absent from the image (L-0014,
  L-0066).
- Coverage gaps around the paths that have actually broken: image bootstrap,
  bundle reconstruction, migration idempotency, queue consumer startup (L-0045).
- CI steps that swallow failure — `2>/dev/null`, `|| true`, a remediation step
  that never reads back its own effect (L-0069) — and steps that parse `az`
  output through a JMESPath nobody dumped raw first (L-0064).
- Workflow triggers: a required check whose workflow never fires on
  `pull_request` reports nothing at all, and nothing at all looks like nothing
  wrong (L-0035).

## Out of scope for this lens

Whether a given test's subject is correct — that is the PR reviewer's job. This
lens is about whether the test could tell us.

## How to report

For each finding, state which direction the check fails in: **false pass** (it
cannot go red) or **false fail** (it goes red for reasons unrelated to
correctness). Both are findings; they need different fixes.
