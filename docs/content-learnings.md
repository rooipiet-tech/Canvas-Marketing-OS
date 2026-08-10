# Content Learnings Index

The content-side analogue of `.compound/index.md` (which tracks build/engineering
learnings). This file tracks recurring **Brand Steward QA (function 02)**
violation patterns and the prompt-rule fixes derived from them.

This is Proposal A from `claude/qa-feedback-loop-proposal-2026-08-05.md`,
stood up 10 Aug 2026 per Pieter's approval to implement Proposal A + Proposal C
now and hold Proposal B (bounded revise-once retry) for roughly a month,
per the proposal doc's own recommendation against unbounded/continuous retry
(reward-hacking risk -- an LLM told "you failed on X, try again" will often
satisfy the check by deleting the offending claim rather than improving it).

**Rule of thumb:** a violation code logged fewer than 3 times in a rolling
window is a data point, not a pattern -- do not change a prompt rule off a
single occurrence. Only promote to an "Accepted pattern" below once a code
recurs 3+ times with a common root cause.

## How an entry gets here

Pulled from Vault `agent_runs` where `function_id = "02-brand-steward-qa"`
and `status = "failed"`, via the saved query in
`services/vault/queries/function02_violations_by_period.sql`. Each
`agent_runs.output.violations` array entry becomes one occurrence.

## Log

- **2026-08-10** -- `unsupported-claim` x1 (task `4003bd89-2a95-5c8b-885d-340139cb961d`,
  daily-signal-loop proof-circuit run, GitHub Actions run #87,
  `deploy-loop-e2e-smoke`, commit `7455173`). This was the proof-circuit's
  intentional live QA_BLOCKED PASS case (round 19b/19c convention -- a
  correct rejection, not a bug). Below the 3-occurrence threshold: logged
  only, no prompt-rule change warranted yet.

## Accepted patterns

*(none yet -- first entry above is below threshold)*
