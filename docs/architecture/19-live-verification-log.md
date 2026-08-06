# 19 — Live Verification Log

*A running record of claims in this documentation set that have been checked
against the **live external systems themselves**, rather than inferred from
the source tree. Each entry records the date, the method, and the outcome.*

**Why this exists.** The rest of this documentation set was reverse-engineered
from source, and `README.md` states the resulting limitation plainly: *"Live
Azure state was not inspected. Deployment claims rest on Bicep, on workflow
definitions, and on comments recording live verification."*

Connected MCP servers do not remove that limitation — they give no Azure
access. What they give is **counterparty verification**: the ability to check
the platform's claims about an external system *from that system's side*.
Where a constant in the code asserts something about Buffer, Canva or
Microsoft, that assertion can now be tested rather than trusted.

**Scope discipline.** Only read-only calls are used. Nothing in this log
writes to, schedules on, or mutates any external account.

---

## Legend

| Status | Meaning |
|---|---|
| ✅ **Confirmed** | Live system agrees with the code |
| ⚠️ **Diverged** | Live system disagrees — the code or the doc is wrong |
| ❌ **Refuted** | The claim is false |
| ⏳ **Pending** | Identified as checkable, not yet run |

---

## 2026-08-06 · Buffer

Method: `get_account`, `list_channels` via the Buffer MCP connector,
organisation *Canvas Intelligence* (`68e5f2187fe9a5263a3509ab`).

| # | Claim in the codebase | Where | Live result | Status |
|---|---|---|---|---|
| B1 | `BUFFER_ORG_ID = "68e5f2187fe9a5263a3509ab"` | `publisher/app/config.py` L44 | account's sole organisation id is `68e5f2187fe9a5263a3509ab` | ✅ |
| B2 | **"Buffer's free-tier plan caps queued posts at 10"** — flagged in code as *"an ASSUMPTION sourced from the GOAL text, not independently verifiable from any file in this repo (DE-3)"* | `publisher/app/config.py` L29–33 | `limits.scheduledPosts: 10` | ✅ **assumption promoted to verified fact** |
| B3 | `BUFFER_LINKEDIN_CHANNEL_ID = "68e73facca3a4e6b746d17b4"` | `publisher/app/config.py` L43 | channel `68e73facca3a4e6b746d17b4`, `service: linkedin`, `Canvas Intelligence` | ✅ |
| B4 | Code comment maps `Facebook=68e74731ca3a4e6b746d2469` | `publisher/app/config.py` L40 | channel `68e74731ca3a4e6b746d2469`, `service: facebook` | ✅ |
| B5 | Code comment maps `X=68e745c6ca3a4e6b746d22b2` | `publisher/app/config.py` L41 | channel `68e745c6ca3a4e6b746d22b2`, `service: twitter` | ✅ |
| B6 | The three channel ids in the weekly loop's `channel_ids` | `loops/weekly-content-loop.yaml` | all three exist, all `isDisconnected: false` | ✅ |
| B7 | Weekly cap of 8 keeps "headroom below the ceiling" of 10 | `loops/weekly-content-loop.yaml` | ceiling confirmed at 10; 8 is genuinely below it | ✅ |
| B8 | Logic App triggers use `South Africa Standard Time` | `infra/modules/scheduling/*.bicep` | account timezone `Africa/Johannesburg` | ✅ consistent |

### B2 is the one that matters

`publisher/app/config.py` carries an unusually candid comment: the free-tier
cap of 10 was taken from the GOAL text and explicitly marked as **not
independently verifiable from any file in this repo**. It is now verified.
The number is right, and the mitigation built around it — a live
`list_queue` count check before every create, rather than trusting the
constant — was the correct call regardless.

### B3–B5 close a known transposition risk

The same file records that *"the GOAL text mistakenly used the X channel id as
the LinkedIn id"*, and that all three ids plus the org id were mapped
defensively so the error "can never recur". The live account confirms the
code's mapping is correct in all three positions: LinkedIn, Facebook and X
each resolve to the id the comment assigns them.

That was a documented near-miss with no external confirmation available at
the time. It now has one.

---

## 2026-08-06 · Buffer — post history (P1, P2)

Method: `list_posts` (read-only, first 100, `hasNextPage: true` — so this is a
sample, not the full history), organisation *Canvas Intelligence*.

| Field | Result |
|---|---|
| posts sampled | 100 (more exist) |
| status | 76 `sent`, 24 `scheduled` |
| `via` | 98 `buffer`, 2 `network` — **zero created via API** |
| createdAt range | 2026-01-19 → 2026-07-29 |
| sentAt range | 2026-03-04 → **2026-08-05** (the day before this check) |
| dueAt range | 2026-03-04 → 2026-09-17 |
| scheduled, by channel | 8 LinkedIn · 8 Facebook · 8 X |

### P1 — ✅ Confirmed: the platform has never published

Every post in the sample was created through Buffer's own interface
(`via: buffer`) or by the network (`via: network`). **None was created through
the API**, which is the only route `mcp-buffer`'s `create_draft` can take.

This independently confirms what `00` and `09` previously asserted from
`PUBLISHER_DRY_RUN` defaulting true and the proof-circuit's forced dry-run.
The claim no longer rests on reading a config default.

### P2 — ❌ Refuted, favourably: there is abundant real Buffer data

`09` TD-11 says 3 of 4 analytics sources are fixtures and only Buffer goes
live. That stands — and the live one has **76 sent posts across five months**
to ingest, not an empty account. The Buffer slice of the KPI rollups would
carry real data the moment the nightly job runs against it.

---

## V1 — The governed pipeline and the real marketing workflow are disjoint

**This is the most consequential thing the live connection revealed.**

Marketing at Canvas Intelligence is happening — actively, on three channels,
with a post sent as recently as the day before this check and 24 more
scheduled out to September. It is being done **by humans, in the Buffer UI,
entirely outside this platform.**

The platform exists to govern marketing publishing. It is not in the path of
any actual marketing publishing.

Everything in `14`'s five-layer authorisation chain — autonomy policy, human
approval, signed gate token, boundary hash verification, replay ledger — 
governs a path that has never carried a real post. Meanwhile the path that
carries every real post has no autonomy policy, no gate token, no content-hash
binding, no `publish_attempts` row, and no kill switch.

That is not a defect in the code. It is a **statement about adoption**, and it
reframes the roadmap: `10` R1 (activate the 20 agents) and R9 (multi-channel)
are not merely capability work — they are what would bring the real workflow
inside the governed one. Until then the control plane is, in the strict sense,
unexercised in production.

---

## V2 — The codified brand rules have never been tested against real output

Function 02's rules were run against the 100 real posts. They are stated in
`functions/02-brand-steward-qa/prompt.md` as blocking violations.

| fn 02 rule | Result against 100 real published posts |
|---|---|
| `link-shortener` — bans `bit.ly`, `lnkd.in`, `tinyurl.com`, `ow.ly`, `buff.ly` | **86 posts would FAIL** — 85 contain `bit.ly`, 1 contains `lnkd.in` |
| `url-utm` — every URL must be a `canvasintelligence.com` link with 3 UTM params | 12 posts carry a Canvas link; only 4 carry any `utm_` → **8 would FAIL** |
| `sa-english-spelling` — no US variants | `center` ×3, `behavior` ×4 → **would FAIL** |
| Roof line (fn 42) — must close on `Your Data. Delivered.` | all 6 occurrences read **`Your data. Delivered.`** — lowercase *d* |

**Read this as a measurement, not an accusation.** There are two honest
readings and only the CMO can choose between them:

1. **The rules are the to-be state.** They describe the brand discipline the
   platform is meant to enforce once it is in the path, and current output
   predates it. Legitimate — but then the rules have never been validated
   against anything, and the `link-shortener` rule in particular would block
   86% of what the brand actually publishes today.
2. **The published content is off-brand.** In which case the platform has been
   correctly encoding a standard nobody is currently meeting, and the gap is
   the point.

Either way: **the platform's brand policy and the brand's actual output have
never been compared until now.** The `bit.ly` finding is the sharpest — a
link shortener is not an accident of tooling here (Buffer's own shortener is
`buff.ly`, which appears zero times), so `bit.ly` is a deliberate, systematic
choice that the codified policy names as a blocking failure.

The roof-line casing divergence is small but telling: function 42's prompt
mandates `Your Data. Delivered.` and every real post writes
`Your data. Delivered.` One of the two is wrong, and it is a one-character fix
in whichever place is wrong.

**Recommended action:** run function 02's `safety_suite.py` over an export of
real published posts as a one-off calibration exercise, before the platform is
put in the publishing path. It is the cheapest possible validation of the
brand rules, it needs no new code, and it would have surfaced all four of
these divergences.


---

## Pending checks — identified, not yet run

Ordered by what they would change in this documentation set.

| # | Question | Source to query | Would resolve |
|---|---|---|---|
| P3 | Does ARM genuinely **replace** a Container App's `env` list on redeploy? | Microsoft Learn `microsoft_docs_search` | **TD-31 is a Priority 1 finding whose core mechanism currently rests on my reading of `container-app.bicep`.** It should rest on Microsoft's own documentation. |
| P4 | Can Key Vault standard tier create/sign/verify Ed25519? | Microsoft Learn | Learning L-0031 and TD-25's remediation path both depend on this being false. Verifying it validates the recommended ES256 switch. |
| P5 | Do any Canva **brand templates** exist? | Canva `search-brand-templates`, `list-brand-kits` | Function 45's carousel path and `bulk_create_from_csv` are template-locked — `template_id` is required. No templates would be a hard blocker on the weekly loop that nothing in the repo records. |
| P6 | Do any Fireflies transcripts exist? | Fireflies `fireflies_get_transcripts` | Function 26 harvests advocacy from transcripts. The integration is unbuilt (`12` I18); if there is also no input, the function is inert on both sides. |
| P7 | Do the four ingestion URLs still resolve? | Microsoft Learn (for the Fabric page); web fetch for the RSS feeds | `fetch_sources.yaml`'s own header asks for exactly this: *"re-verify this liveness periodically, since a renamed/retired page silently narrows AC-24's guarantee rather than failing loudly."* |
| P8 | Is Semrush a realistic future signal source? | Semrush `domain_overview` | `contracts/vault-api.yaml`'s own example payload cites `source: "semrush"`, but no integration exists (`12` I18). |

**P3 is the highest priority.** A Priority 1 finding should not depend on one
person's reading of an IaC template when the authoritative documentation is
one query away.

---

## What this cannot fix

The connectors verify **counterparties**, not the platform. They cannot:

- inspect live Azure state (`cmos-dev` resource group, Container App revisions, actual env vars) — so **TD-31 cannot be confirmed as currently-broken or currently-fine**, only its mechanism verified;
- read the Postgres instance, so no claim about live data volumes or row states can be checked;
- read Application Insights, so no telemetry or trace claim can be checked;
- confirm whether `MCP_WEB_LIVE_MODE` is set on `ca-mcp-web` right now — the single most consequential open question in this documentation set.

Those all require `az` CLI access to the subscription. Until then, the
limitation stated in `README.md` stands unchanged.

## Operational note

The MCP connectors were observed disconnecting and reconnecting repeatedly
during this session. Any batch of checks should be written to tolerate a
mid-run drop and resume, rather than assuming a stable session.
