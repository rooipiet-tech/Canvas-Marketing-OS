# 21 — Brand-Safety Calibration Report, 2026-09-07

**What this is.** `09-technical-debt.md` TD-32 and `19-live-verification-log.md`
V2 both recommended the same next step: run function 02's actual checker,
`services/registry/safety_suite.py`, over a real export of published posts,
"as a one-off calibration exercise, before the platform is put in the
publishing path." V2's own numbers were a manual, ad-hoc spot-check against
100 posts pulled once and read by hand. This document is that recommendation
carried out formally — the checker itself, run against a fresh, versioned
export, with the raw output committed alongside it so the result can be
re-run and audited rather than taken on faith.

**What this is not.** A policy decision. TD-32 already names this "a
decision for the CMO, not for engineering," and nothing here chooses between
the two readings TD-32 lays out. `22-brand-policy-reconciliation-memo.md` is
where that choice gets made. This document only answers: *what does the
checker actually say, run for real, against real output?*

**Inputs.** `services/registry/fixtures/calibration/2026-09-07-buffer-export/`
— 100 real, already-published Canvas Intelligence posts pulled live from the
connected Buffer organisation on 2026-09-07 (LinkedIn, Facebook and
Twitter/X, the 100 most recently *sent* posts across all three channels,
spanning 2026-03-04 to 2026-09-02). See that directory's `README.md` for the
exact pull method and the reproduction command. This export is larger and
more channel-diverse than V2's original 100-post sample (V2 does not record
which channel(s) it drew from); differences between the two sets of numbers
below are most likely sampling differences, not a moving target — the
underlying practice (bit.ly on nearly every post, US spellings in-house)
reads as stable across both.

**Command run:**

```sh
python services/registry/safety_suite.py \
  --dir services/registry/fixtures/calibration/2026-09-07-buffer-export/posts
```

**Result:** exit 1. `184` findings across `84` of the `100` files (`16`
clean). Full raw output, unedited, is committed at
`services/registry/fixtures/calibration/2026-09-07-buffer-export/safety_suite_findings.txt`.

---

## 1. Results, by rule

| Rule | Files affected | Lines flagged | In TD-32 / V2's original table? |
|---|---:|---:|---|
| `link-shortener` | 81 / 100 | 81 | Yes — V2 recorded 86/100 (85 bit.ly + 1 lnkd.in) |
| `missing-cta` | 80 / 100 | 80 | **No** — not measured by the original manual check |
| `unsupported-claim` | 15 / 100 | 15 | **No** — not measured by the original manual check |
| `sa-english-spelling` | 7 / 100 | 7 | Yes — V2 recorded `center` ×3, `behavior` ×4 |
| `url-utm` | 1 / 100 | 1 | Yes — V2 recorded 8/100 fails (of 12 posts carrying a Canvas link) |

The first two rows below are the ones TD-32 already flagged and this run
confirms with real numbers behind them. The `missing-cta` and
`unsupported-claim` rows are new information this formal run surfaces that
the original hand-check did not measure at all — see §3.

### 1.1 `link-shortener` — 81 of 100 files fail

```
bit.ly   80 files
lnkd.in   1 file
```

**`buff.ly` — Buffer's own shortener — appears in zero of the 100 posts, and
in zero of the raw text scanned for any URL at all.** Across all 100 posts,
30 URLs appear in total; 28 of those 30 are `bit.ly` links, 1 is `lnkd.in`,
and exactly 1 is a direct `canvasintelligence.com` link. That is not noise —
it is the brand's editorial link tooling, used almost everywhere a link
appears, and it is exactly the pattern `link-shortener` exists to block. See
`22-brand-policy-reconciliation-memo.md` §2 for why this is the sharpest data
point in the whole calibration.

### 1.2 `sa-english-spelling` — 7 of 100 files fail

```
center     3 occurrences  (posts 001, 002, 014)
behavior   4 occurrences  (posts 031, 033, 058, 059)
```

This matches V2's manual count exactly (`center` ×3, `behavior` ×4). Two
different measurement methods, five weeks apart in method if not necessarily
in underlying content, landed on the identical number — the strongest
internal-consistency signal in this whole exercise.

### 1.3 `url-utm` — 1 of 100 files fail, but the finding is different from V2's

Only one non-shortened, non-`canvasintelligence.com` URL exists in the whole
export (rather than V2's "12 posts carry a Canvas link, 4 carry any
`utm_`"), because in this export almost every link is already a `bit.ly`
shortener (§1.1) and `check_urls` deliberately does not double-count a URL
already reported as `link-shortener`. The one link that does reach this
check is a real, already-UTM-tagged Canvas link that fails only because it
omits the `www.` subdomain the rule's `CANVAS_URL_PREFIX` requires:

```
'https://canvasintelligence.com/?utm_source=facebook&utm_medium=social&utm_campaign=social_calendar_2026_27&utm_content=02-microsoft-fabric'
is not a full https://www.canvasintelligence.com/... link
```

This is worth a maintainer's attention on its own terms — it is a checker
brittleness (a `www.`-only prefix match against a domain the brand
apparently also serves without it), not a brand-practice question, and not
something this document resolves. Flagging it here rather than silently
folding it into the CMO-facing count, since fixing it is an engineering
call, not a policy one.

### 1.4 Roof line — not a `safety_suite.py` rule; resolved separately

`safety_suite.py` does not check the roof line at all — that check lives in
each writer function's own `tool_check.py` (e.g.
`functions/42-linkedin-post-writer/tool_check.py`'s `ROOF_LINE` constant).
The roof-line casing question TD-32 and V2 both raised is closed in this
same PR — see `09-technical-debt.md` TD-32's updated entry and
`19-live-verification-log.md` V2's follow-up note for the evidence
(`docs/positioning.md`'s explicit, "(keep)"-flagged tagline). It did not need
a calibration run; it needed one citation.

---

## 2. What this run adds beyond TD-32 / V2

### `missing-cta` — 80 of 100 files fail

`check_cta` looks for one of a fixed list of phrases (`"book a"`, `"get in
touch"`, `"contact us"`, `"canvasintelligence.com"`, …). 80 of the 100 real
posts contain none of them. Reading the actual flagged posts, this is not
mostly "no call to action" in the plain-English sense — most of these posts
do end on an instruction to click a link — it is that the real copy's CTA
phrasing (and the fact that the link itself is a bare `bit.ly` URL, not the
`canvasintelligence.com` domain string the checker also accepts as a CTA
marker) does not overlap with the checker's fixed lexicon. This is the same
underlying fact as §1.1 wearing a different rule's name: once a link is a
`bit.ly` shortener, `check_cta`'s `"canvasintelligence.com"` marker cannot
fire on it either. Recorded here as a finding, not a recommendation — it is
in scope for the same CMO decision as the three TD-32 items, not a separate
one, since it traces back to the same bit.ly practice.

### `unsupported-claim` — 15 of 100 files fail

The dominant flagged term is `cutting-edge` (appears in the majority of the
15), used exactly the way `positioning.md`'s tone rule (line 92, "proof over
platitude") describes a bare superlative: no client, number, or artefact in
the same sentence. Example (post 001): *"Our team utilise industry best
practices and cutting-edge technologies and tools to harness the full
potential of your data."*

---

## 3. Reconciling with V2's original figures

| Finding | V2 (manual, one-off) | This run (formal, versioned) |
|---|---|---|
| `link-shortener` | 86/100 fail (85 bit.ly, 1 lnkd.in) | 81/100 fail (80 bit.ly, 1 lnkd.in) |
| `sa-english-spelling` | `center` ×3, `behavior` ×4 | Identical: `center` ×3, `behavior` ×4 |
| `url-utm` | 8/100 fail (of 12 carrying a Canvas link) | 1/100 fail (only 1 non-shortened Canvas-domain link exists in this export at all) |
| `buff.ly` occurrences | 0 | 0 |

The `sa-english-spelling` match being exact, and `link-shortener` landing
within 5 posts of V2's figure on a differently-drawn, larger, multi-channel
sample, both point the same way: **this is a stable, systematic pattern in
how Canvas Intelligence actually publishes, not an artefact of either
sample.** The `url-utm` gap is fully explained by §1.3 above (this sample
simply contains far fewer non-shortened, non-Canvas-domain URLs to trigger
it on) and is not evidence the underlying practice changed.

**What has not changed, and is the reason this document exists rather than
just updating V2's table:** `qa_review_handler`'s `pass: false` verdict is
still terminal, still with no override, and TD-01 having activated the
agents means this checker (or its LLM-judged sibling, function 02's prompt)
sits between every future draft and Buffer. A CMO decision is still required
before that gate goes live against real practice — see
`22-brand-policy-reconciliation-memo.md`.
