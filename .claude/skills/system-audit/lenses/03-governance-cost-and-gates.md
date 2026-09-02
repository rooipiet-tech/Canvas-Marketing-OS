---
id: 03-governance-cost-and-gates
title: Governance, gates and cost control
---

# Lens: governance, gates and cost control

## In scope

- The gate path end to end: `services/gatekeeper`, `services/publisher`,
  `contracts/gate-token`, the console's approval surface, and the loop
  definitions that route work through them. Can any path reach a publish
  without the gate it is supposed to pass?
- Gate tokens: issuance, scope, expiry, replay, and what happens to a token when
  the run it was issued for dead-letters.
- Cost and metering: `services/model-gateway`'s metering writes, the routing
  table, per-run and per-loop spend. A metering write that can fail silently, a
  loop with no bound on fan-out, or a retry path that re-charges are all in
  scope. So is a gateway route pointing at a model tier nobody meant to pay for.
- Approval gates in the deploy workflows: which steps sit behind them, and
  whether a free idempotent preflight has been trapped inside one (L-0008). Also
  stale runs holding a shared `concurrency:` group and cancelling their
  successors (L-0072).
- Dead-lettering and quiet degradation in the orchestrator: a run that reports
  success while producing nothing, a stage that reports terminal on failure, a
  per-source `try/except` that turns a broken dependency into a short result.
- Human-in-the-loop assumptions in `docs/architecture/07-operating-model.md`
  that the code does not actually enforce.

## Out of scope for this lens

Secrets and permissions (lens 01), IaC drift (lens 02).

## Specific things this repository has got wrong before

- A completion succeeding while its metering write 500'd the request (L-0027).
- A smoke test given a privileged shortcut entry in shared policy config rather
  than exercising the real approval path (L-0029).
- A poll misreading its own `TERMINAL_STATES` set, so "1/7 stages terminal" was
  read as a stuck stage for four fix attempts (L-0076).
