# 22 — Brand Policy Reconciliation Memo

**Status: decision required. No prompt, rule, or code change has been made
against either reading below.** This document is the reconciliation
`09-technical-debt.md` TD-32 and `19-live-verification-log.md` V2 both defer:
function 02's (`functions/02-brand-steward-qa/prompt.md`) codified brand
rules disagree with what Canvas Intelligence actually publishes, on three of
its checks, and TD-32 names this explicitly as "a decision for the CMO, not
for engineering." This memo lays out the two readings the register already
names, with the calibration data behind each, and does not choose between
them. Part 2 sketches what changes once a decision is made, for either
reading — detailed enough to execute, **but not authorised to start.**

The underlying data is in
`21-brand-safety-calibration-2026-09-07.md` (`services/registry/safety_suite.py`
run formally, versioned, over 100 real published posts pulled fresh from the
live Buffer organisation on 2026-09-07) and reproduces the direction, if not
every digit, of `19-live-verification-log.md` V2's original manual
spot-check. This memo does not re-derive the numbers; it interprets them.

---

## Part 1 — The decision

### 1.1 What is actually in tension

Three of function 02's blocking rules, run formally against real,
already-published Canvas Intelligence output, fail on the large majority of
that output:

| Rule (function 02, `prompt.md`) | What it blocks | Real-output failure rate (this calibration) |
|---|---|---:|
| `link-shortener` | `bit.ly`, `lnkd.in`, `tinyurl.com`, `ow.ly`, `buff.ly` or equivalent, anywhere in the draft | 81 of 100 posts |
| `sa-english-spelling` | US spelling variants (`center`, `behavior`, `productize`, …) | 7 of 100 posts |
| `url-utm` | Every URL must be a full `canvasintelligence.com` link carrying `utm_source`/`utm_medium`/`utm_campaign` | Effectively untestable in this sample — see `21` §1.3 — but the underlying cause (almost every link is a shortener, so almost no link is ever a full, UTM-tagged Canvas link) is the same fact as the row above |

`qa_review_handler`'s verdict is terminal: `pass: false` transitions the task
to `FAILED` with reason `qa_blocked` and never calls `advance_dependents`.
There is no override, by design (`prompt.md`'s own rules: "Never rewrite the
draft... Never resolve an uncleared client name by removing it yourself").
That is the right design for a QA gate — a gate that can be silently
overridden is not a gate — but it means these three rules, as written today,
would block the overwhelming majority of drafts shaped like the brand's own
history the day this checker sits in the real publishing path.

### 1.2 The sharpest data point: `bit.ly`, not `buff.ly`

Buffer — the platform Canvas Intelligence schedules and publishes every one
of these posts through — has its own first-party link shortener, `buff.ly`.
It is available for free on every Buffer plan, it is the shortener Buffer's
own product nudges users toward, and it is explicitly named in function 02's
own ban list right alongside `bit.ly` — the rule does not exempt it.

**`buff.ly` occurs zero times across all 100 posts in this calibration, and
zero times across every URL scanned in the export (30 total).** `bit.ly`
occurs in 80 of the 100. This is not "a tool the team forgot to swap for the
in-house one" — if that were the story, `buff.ly` would show up at least
sometimes, on whichever posts someone remembered to update. Its total
absence, against `bit.ly`'s near-total presence, says this is a **deliberate
and consistent editorial choice**, made post after post, over the six-month
window this export spans (2026-03-04 to 2026-09-02) — not drift, not an
accident, not a stale default. Whoever schedules these posts picked `bit.ly`
and kept picking it. Function 02's codified policy — written, presumably,
without reference to this pattern — blocks the choice that was actually,
consistently made.

### 1.3 Reading A — the rules are the to-be state

Function 02's rules describe the brand discipline the platform is meant to
enforce **once it is in the path**, and the 100 real posts calibrated here
all predate that policy existing at all. Under this reading:

- The policy is legitimate and should stand as written.
- But it has never been validated against anything until this calibration,
  and — taken at face value — it would reject 81%+ of what the brand
  actually ships today, on `link-shortener` alone.
- `sa-english-spelling`'s 7% failure rate is small enough that this reading
  costs little there; `link-shortener`'s 81% is the expensive part of this
  reading, because it implies a near-total stop the day TD-01's activated
  agents put function 02 in the real publishing path — every future post
  that uses a bit.ly link (which, on six months of precedent, is most of
  them) blocks at the gate, with no override, until whoever schedules posts
  changes habits to match the new rule.
- Under this reading, the fix is **communication and workflow, not code**:
  tell whoever runs the Buffer account that `bit.ly` is now blocked, and
  make sure the posts function 02 will actually see (drafts from functions
  39/41/42/43/45/46/47/52 — the ones that already write `buff.ly`-and-UTM-
  compliant copy in their own prompts and fixtures, per `21`'s cross-check
  of `functions/*/prompt.md`) are what gets scheduled, rather than whatever
  workflow produced the last six months of real posts.

### 1.4 Reading B — the published content is off-brand

The platform has been correctly encoding a standard nobody currently meets,
and the gap is the point — the whole reason a QA gate exists is to catch
practice drifting from policy, and it just did, on the first real test.
Under this reading:

- Nothing about function 02's rules changes.
- The remediation is entirely outside engineering and outside this
  repository: whoever manages the Buffer account and writes real posts today
  needs to stop using `bit.ly`, start using either `buff.ly` or full,
  UTM-tagged `canvasintelligence.com` links, and correct the two US-spelling
  habits (`center` → `centre`, `behavior` → `behaviour`).
- This reading has a real cost the register should not understate: UTM
  attribution (the entire reason `link-shortener` and `url-utm` exist as
  rules — see `positioning.md`'s own tone rule and `functions/02-brand-steward-qa/prompt.md`
  §2/§5) has apparently not been in place on the majority of Canvas
  Intelligence's own published social output for at least six months. If
  `analytics.utm_campaign_map` or the KPI rollups TD-34 discusses were ever
  expected to attribute *this* historical output back to a campaign, Reading
  B implies they structurally could not have.

### 1.5 What this memo is not deciding

Both readings are legitimate, and nothing in the calibration data breaks the
tie — it only sharpens what is at stake in each direction. This is exactly
the shape of decision TD-35 and TD-36 already route to a business owner
rather than to engineering, and TD-32 names the same owner: **the CMO
decides which of §1.3 / §1.4 holds, or some blend (e.g. `sa-english-spelling`
under Reading B, `link-shortener` under Reading A, given how differently
sized their failure rates are) — engineering does not choose on its
behalf, and `functions/02-brand-steward-qa/prompt.md` is not touched by this
PR.**

---

## Part 2 — Follow-up spec, for whichever reading wins

**Not authorised to start. Both branches are sketched only so whichever one
the CMO picks can move straight to execution without a further design pass.
No code in this PR implements either.**

### 2.1 If Reading A wins (rules stand; practice must change)

- No change to `functions/02-brand-steward-qa/prompt.md` or
  `services/registry/safety_suite.py`.
- Operational, not engineering: whoever schedules Buffer posts switches from
  `bit.ly` to either (a) Buffer's own `buff.ly` (fastest — it is already
  free, in-product, and the rule already permits nothing else, since
  `buff.ly` is itself named in the ban list — **this branch of Reading A
  requires the rule to explicitly carve out an exception for Buffer's own
  shortener, which is a one-line, CMO-approved change to `prompt.md`'s §2
  the moment this reading is chosen** — or (b) full
  `https://www.canvasintelligence.com/...` links with `utm_source`,
  `utm_medium`, `utm_campaign` set by hand until function 02 is generating
  the copy itself.
- Fix the two spelling habits at the source (whatever tool or template
  produces the human-scheduled posts today) — no code change, since
  `sa-english-spelling`'s word list already matches `positioning.md`'s
  attested forms.
- Once TD-01's activated agents are the ones producing drafts end to end
  (rather than a human scheduling by hand), this branch resolves itself: the
  function prompts (39/41/42/43/45/46/47/52) already write compliant copy —
  confirmed by their own `evals/*.json` fixtures, all of which use
  `canvasintelligence.com` links with full UTM parameters and zero
  shorteners.

### 2.2 If Reading B wins (rules were wrong; codify current practice)

Three independent rule edits, each scoped and each requiring the same
CMO sign-off TD-32 names — not a blanket "relax the checker":

- **`link-shortener`**: remove `bit.ly` (and/or `lnkd.in`) from the ban list
  in both `functions/02-brand-steward-qa/prompt.md` §2 and
  `services/registry/safety_suite.py`'s `LINK_SHORTENERS` tuple — the two
  copies of this rule must move together, per CLAUDE.md hard rule 10
  ("a shared-mechanism fix is a bug-class fix"); a rule relaxed in the prompt
  but not the deterministic checker (or vice versa) reintroduces exactly the
  policy/practice gap this memo exists to close, just in the other
  direction. The UTM-attribution cost noted in §1.4 does not go away under
  this branch — it would need its own follow-up (how does `bit.ly` traffic
  get attributed at all?), out of scope here.
- **`sa-english-spelling`**: given the small failure rate (7%) and that
  `positioning.md` itself explicitly attests South African spellings
  (`productised`, `behaviour` — see `positioning.md` §5's "Say / don't say"
  and `safety_suite.py`'s own module docstring, which cites it by name), this
  is the rule least likely to be the one Reading B applies to. If chosen
  anyway, same two-file update as above (`prompt.md` §3,
  `US_TO_SA_SPELLING` in `safety_suite.py`).
- **`url-utm`**: separately from the policy question, `21` §1.3 already
  flags that the rule's `CANVAS_URL_PREFIX` requires a `www.` subdomain the
  brand's own domain does not always carry — fix that regardless of which
  reading wins; it is an engineering bug, not a policy call, and belongs in
  its own small PR.

### 2.3 Out of scope for either branch

- `missing-cta` and `unsupported-claim` (`21` §2) are not part of this
  decision — they were not named in TD-32 or V2, and this memo does not
  expand the CMO's decision to cover them. If they warrant the same
  treatment, that is a separate finding for a separate memo.
- Nothing here proposes changing `qa_review_handler`'s terminal, no-override
  verdict design. That design is correct regardless of which reading wins;
  what changes is only which set of inputs the gate is calibrated against.
