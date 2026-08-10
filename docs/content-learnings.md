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

- **2026-08-10 (round 34)** -- `fabricated-proof-point` x6 and
  `misstated-approved-fact` x4 across all 6 Wednesday drafts in one
  `qa-review-fact-check` run (task `a1129f57-d475-56fd-9021-dc0dc38cc90f`,
  `la-weekly-planning-trigger` run `08584152368952770981690166934CU30`,
  13:56:48 UTC): `draft-insight-to-story` (`95cc1618`) x1
  fabricated-proof-point; `draft-executive-ghostwrite` (`b209c869`) x2
  fabricated-proof-point; `draft-carousel-post` (`87ec5802`) x2
  fabricated-proof-point; `draft-newsletter` (`648989f5`) x1
  misstated-approved-fact; `draft-case-study` (`bd7b5e3f`) x2
  fabricated-proof-point + x1 misstated-approved-fact;
  `draft-content-repurpose` (`dbee240d`) x1 misstated-approved-fact + x1
  fabricated-proof-point. Root cause: all 9 drafting prompts
  (`functions/{26,39,41,42,43,45,46,47,52}/prompt.md`) copy
  `docs/positioning.md` section 5's messaging-house table verbatim, whose
  Productised-speed **Message** column reads "first insight in days,
  go-live in weeks" -- a duration with no backing entry in the pillar's own
  **Lead proof** column, and no approved proof point anywhere covers a
  specific time-to-insight/time-to-go-live figure. Both flagged drafts
  asserted this phrase (or a close paraphrase) as established fact.
  **Promoted to Accepted pattern below and fixed same-day** -- see PR
  (`content/proof-point-message-vs-fact-guard`).
- **2026-08-10 (round 34)** -- `uncleared-client-reference` x1
  (`draft-case-study`, `bd7b5e3f`, `qa-review-brand-steward` task
  `af00fcb6-84ce-5b1d-afb4-308a560e08e9`, same run). Draft text itself
  names no client, logo or proper noun -- reads as "a multi-entity
  financial services group." Possible combination-test catch (identifying
  by elimination if financial services is a narrow live vertical) rather
  than a literal name leak; possible false positive in a category PR #97's
  backstop doesn't cover. **Logged only -- unresolved, needs a human check
  of how many financial-services-vertical clients are currently live
  before this can be classified either way. Do not treat as a pattern or
  build a fix until that's settled.**

- **2026-08-10 (round 34, second fire)** -- `model_response_json_parse_failed`
  x6 (all 6 Wednesday drafts, `la-weekly-planning-trigger` run
  `08584152278723035727763064687CU11`, 16:23:33 UTC). Not a QA-gate
  violation -- the model's own output failed to parse as JSON before
  reaching QA, so the affected `agent_runs` rows never left `status =
  "running"`. Root cause confirmed via Log Analytics
  (`log-cmos-dev`/`ContainerAppConsoleLogs_CL`, KQL `parse_json(Log_s)` +
  `substring()` on `response_preview` at the exact `char N` offset each
  `json.JSONDecodeError` reported): the model embeds a literal, unescaped
  `"` character directly around quoted CFO-survey phrases -- "different
  number for the same question" or "which number is right" -- inside a
  JSON string value it is generating, which `_parse_json_content` (in
  `services/orchestrator/orchestrator/dispatch.py`) reads as a premature
  string terminator, producing `Expecting ',' delimiter` errors mid-document
  (char 917, 870, 890, 987, 1873, 1330 across the 6 instances -- not at the
  end, ruling out truncation; not after a complete value, ruling out
  trailing-content). These exact phrases are shown as literal quoted bullet
  examples in each drafting prompt's own "Who you are writing to" section
  (pre-existing content, not something introduced by the round-34 fact-guard
  fix above) -- the model was faithfully reproducing them wrapped in
  un-escaped double quotes. **Promoted to Accepted pattern below and fixed
  same-day** -- see PR (`content/json-quote-escape-guard`).

## Accepted patterns

- **CFO-survey quoted phrases must never be rendered with literal double
  quote marks inside JSON string output.** Confirmed 2026-08-10 (round 34,
  second fire), 6 occurrences in one run, one common root cause (see log
  above). Fix: every drafting prompt that quotes CFO-survey pain language
  directly in its "Who you are writing to" section now has an explicit
  "JSON safety" guard immediately before those quoted bullets, instructing
  the model to attribute the language naturally (no quote marks) or use
  single quotes for emphasis instead of double quotes. Applied to
  `functions/{39,43,45,46,47,52}/prompt.md` 2026-08-10.

- **"First insight in days, go-live in weeks" (Productised speed pillar
  Message) must never be asserted as fact.** Confirmed 2026-08-10 (round
  34), 6 occurrences in one run, one common root cause (see log above).
  Fix: every drafting prompt that carries the messaging-house table now
  has an explicit guard directly beneath it, plus a parallel guard
  requiring CFO-survey pain language ("more than 3 days a month" etc.) to
  stay attributed as voice-of-customer research rather than restated as an
  unattributed statistic. Applied to `functions/{39,43,45,46,47,52}/
  prompt.md` (the 6 Wednesday-draft writers this pattern was actually
  observed in) 2026-08-10. **Not yet applied to `functions/{26,41,42}/
  prompt.md`**, which carry the same table but weren't implicated in this
  run -- worth a follow-up pass if the pattern recurs there.
