# Review packet — function 48, fact-check verdict (TD-35)

For Pieter. This exists so the actual review — the one this function's own
prompt header has required since 6 Aug 2026 — takes minutes, not an
afternoon of reading `dispatch.py`. It does not ask you to approve
anything by reading it; it asks you to read `prompt.md` itself (it's ~360
lines, most of it worked examples) and then flip one flag, described at
the end.

## Why this packet exists now, and what it does not claim

`prompt.md` currently carries a note dated 2 Sep 2026 saying you signed
this off as settled QA policy. **Engineering cannot verify that a review
actually happened** — the note is prose an earlier session wrote into the
file, and prose in a markdown file is exactly the kind of self-reported
approval this whole exercise exists to not trust. Whether that 2 Sep
review happened or not, this packet does not treat it as settled: as of
this change, **no engineering process infers approval from the prompt
file's text at all**. The Thursday fact-check tasks
(`qa-review-fact-check`, `services/orchestrator/orchestrator/dispatch.py`'s
`qa_review_fact_check_handler`) now read exactly one flag —
`policies/fact-check-gate.yaml`'s `approved` — and it ships `false`.
Every one of those tasks fails closed (blocked, logged, a Teams card
posted — never silently skipped or silently passed) until that flag is
flipped by a human, after an actual review. See "What changed in this PR"
at the end for exactly what that means operationally.

## What the prompt currently enforces

One criterion, quoted from `weekly-content-loop.yaml` itself:

> A fact-check verdict confirms every proof point in every Wednesday draft
> traces to a cited source, with no fabricated claim surviving downstream
> to scheduling or publication.

It checks **traceability**, not tone, brand voice, spelling, CTAs, or
client-naming — those are function 02 (Brand Steward)'s job and run as a
separate, independent gate. A draft can pass one and fail the other.

Every specific, checkable claim (a number, a date, a named certification,
a named capability) is checked against four lists:

| List | Source | What it covers |
|---|---|---|
| **A** | `docs/positioning.md` §§3, 5 | 9 company-wide facts (99.5% reconciliation, month-end 2 days faster, etc.) + one lead proof point per content pillar |
| **B** | `docs/positioning.md` §1 | The revenue model itself — CoEaaS is ~80% of revenue and the headline offer; Canvas for BuildSmart is under 1% and never the flagship |
| **C** | `docs/positioning.md` §4 | Approved CFO-survey pain language ("more than 3 days a month," "which number is right"), when attributed to the audience, not asserted as Canvas's own result |
| **D** | This week's `proof_points` (function 41's `{claim, source}` pairs) | This week's own cited market evidence — empty in a week with none |

A claim matches a list only as a **faithful paraphrase** — restating it
without strengthening a number, an earlier date, or a broader scope. There
is no partial pass: `pass` is `true` only when `violations` is empty.

Three violation codes, one per failure shape:

- `fabricated-proof-point` — traces to nothing in A–D.
- `misstated-approved-fact` — traces to something in A–D, but sharpened
  beyond a faithful paraphrase.
- `revenue-model-misstatement` — elevates BuildSmart (or any one platform)
  above CoEaaS, or calls it the flagship.

## Concrete before/after, pulled from the function's own golden evals

These are the actual fixtures under `evals/` — not illustrations, the
literal test inputs and expected verdicts CI runs today.

**Passes — a standing fact, faithfully restated** (`task-01`):
> "Every figure reconciles to source at 99.5% or better, with severity-1
> treatment on any variance." → `pass: true`

**Blocked — no source at all** (`task-02`):
> "Our platform cut licensing spend by 63% across 200 seats in the first
> quarter." → `fabricated-proof-point` — neither number is in any
> approved list, and no evidence was supplied.

**Blocked — the costliest failure mode by design** (`task-06`):
> "Canvas for BuildSmart is our flagship product and our most
> differentiated asset, live since 2013." → `revenue-model-misstatement` —
> BuildSmart is under 1% of revenue and flat; this is the exact
> misstatement the 3 Aug 2026 positioning correction was written to fix.

**Passes — this week's own cited evidence** (`task-03`), given
`proof_points` containing `{"claim": "Reporting cycles at a listed South
African group fell from nine days to two", "source": "moneyweb.co.za"}`:
> "Reporting cycles at one listed South African group fell from nine days
> to two." → `pass: true`

**Blocked — the same evidence, sharpened** (`task-04`), same
`proof_points` as above:
> "Reporting cycles at one listed South African group fell from nine days
> to **zero** days." → `misstated-approved-fact` — the cited pair says
> "two," not "zero."

**Blocked — evidence exists, but not for this claim** (`task-05`), same
`proof_points`:
> "Fabric adoption among South African insurers grew 41% year on year." →
> `fabricated-proof-point` — a source URL being present elsewhere in
> `proof_points` is not a citation for a claim it doesn't contain.

**Passes — attributed survey language, not a Canvas outcome claim**
(`task-07`):
> "Finance teams tell us they lose more than three days a month to
> reporting, cleaning data and reconciling systems." → `pass: true`
> (would **block** as `misstated-approved-fact` if written as "we cut
> reconciliation from 3 days to zero" — that reframes a reported audience
> experience as a measured Canvas result).

**Passes — a pillar's own lead proof, already anonymised** (`task-08`):
> "A multinational logistics group runs 40+ business units and 14+ ERP
> systems on one governed lakehouse." → `pass: true` (the real client
> behind this number is never named — naming it would be function 02's
> `uncleared-client-reference`, not a fact-check failure, per the
> standing confidentiality note in `prompt.md`).

## The two failure modes the register names (TD-35)

Both are real, both have already happened at least once in this
function's short life, and the corrections are all logged in `prompt.md`
itself (rounds 24 and 34, the 1 Sep and 2 Sep change/sign-off notes):

- **Over-strict — blocks good content.** Twice, *every one of the six
  Wednesday drafts failed in the same live run* because the standing
  lists (a snapshot of `positioning.md`) didn't yet cover a pillar's lead
  proof (round 24, 7 Aug) or the CFO-survey pain language every drafting
  prompt is instructed to open with (round 34, 10 Aug). Both gaps are
  closed now, but the failure shape — a prompt-level instruction change
  outrunning this function's lists — can recur with any future drafting
  change.
- **Under-strict — lets a fabricated number reach a client's inbox or a
  public post.** The function's own "Known limitations" section names
  the gap that's still open: a narrative fabrication carrying no checkable
  number (an invented, unnamed client story) passes today, because this
  check only judges claims a reader could verify against a specific fact.
  It reached a real draft once (round 34, 10 Aug — "a mid-market group
  running multiple entities," an unapproved third case study). Widening
  the check to catch this is explicitly out of scope without your
  sign-off, per the prompt's own header rule.

## What changed in this PR — mechanics, not policy

Nothing about the prompt's criterion, lists, or violation codes changed.
What changed is enforcement:

- `policies/fact-check-gate.yaml` — one new file, one field: `approved:
  false`. This is the only thing `qa_review_fact_check_handler` and the
  Thursday QA retry loop's fact-check re-check consult.
- While `approved` is `false`, every `qa-review-fact-check` task fails
  closed immediately — no model call, no cost — with violation code
  `fact-check-policy-not-approved`, transitions to `FAILED`/`QA_BLOCKED`
  exactly like a real content violation, and posts the same kind of Teams
  card a blocked draft would. Friday's schedule/publish tasks already
  refuse to proceed without a passing review on the same lineage, so
  nothing reaches Buffer or the newsletter while this flag is off.
  A deliberately-undrafted week (no executive configured that week) is
  unaffected — there's nothing to fact-check either way.
- Flip `approved: true` in `policies/fact-check-gate.yaml` once you've
  actually reviewed `prompt.md` and confirmed it — through a PR review
  comment, an email, whatever leaves a trail this repository can point to
  next time someone asks "did this actually get reviewed?" Nothing else
  needs to change; the code path this replaces is the one the "signed
  off" note already assumed was live.
