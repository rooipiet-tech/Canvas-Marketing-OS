# Autonomy policy — level 0..4 semantics

`autonomy.yaml` maps a `(function_id, action_class)` pair to an autonomy
`level` between 0 and 4. Gatekeeper loads it once at startup; a malformed
entry is a load-time error, never a silently-ignored line.

No autonomy blueprint exists anywhere in this repository (verified by
repo-wide grep for `autonomy`, `level 0`, `function_id`). The five levels
below are therefore this session's invented first-draft convention, stated
here in human-readable form so a later session can adopt or supersede it
deliberately rather than by accident.

## The five levels

- **level 0 — blocked always.** The Gatekeeper refuses every request for
  this function; no approval, from anyone, can unblock it. A decision row
  is still written (`reason` = `level_0_blocked`).
- **level 1 — approval-required (single approver).** Exactly one human
  approve/reject click is required before a gate token is ever issued. The
  first gate-check escalates and raises an approval request.
- **level 2 — approval-required (elevated).** Semantically "higher stakes
  than level 1"; this session implements it with the **same** single-
  approver mechanism as level 1, reserving a real quorum/second-approver
  rule for a future session. The distinction is recorded in the audit
  reason (`level_2_requires_approval`) so the two are never conflated.
- **level 3 — auto-approved-and-audited.** No human in the loop. The gate
  is granted automatically and a gate token is issued, but a decision row
  is always written first so the action is fully reconstructable.
- **level 4 — fully-autonomous passthrough.** No human in the loop and no
  policy friction; the call passes straight through and is logged (a
  decision row is still written — "logged" here means audited, not
  unrecorded).

## Fail-closed default

`default_level: 0`. Any `(function_id, action_class)` pair absent from
`entries` resolves to level 0 and is blocked. Adding a new function is an
explicit, reviewable edit to `autonomy.yaml` — never an implicit grant.

## Entry shape

Every entry must carry `function_id` (string), `action_class` (string) and
`level` (integer 0..4). `description` is optional but strongly encouraged.
Duplicate `(function_id, action_class)` pairs are a load-time error, so
two entries can never silently disagree about a level.
