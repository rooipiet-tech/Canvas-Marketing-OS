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

## 2026-08-06 · Microsoft documentation (P3, P4)

Method note, stated up front because it affects how much weight these carry:
the Microsoft Learn MCP connector returned `MCP error -32003: MCP tool call
requires approval` on every attempt, and direct fetches of
`learn.microsoft.com` return **HTTP 403** through this environment's proxy —
for both `WebFetch` and `curl`. Both answers below were therefore obtained by
**web search over the Microsoft Learn corpus**, which returned the relevant
Learn pages and their wording, rather than by fetching those pages directly.

That is a weaker citation than reading the page. It is recorded as such. The
underlying pages are named so anyone with unproxied access can confirm in a
minute:

- `learn.microsoft.com/en-us/azure/azure-resource-manager/templates/deployment-modes`
- `learn.microsoft.com/en-us/azure/key-vault/keys/about-keys-details`

### P3 — ✅ Confirmed: incremental mode is incremental *per resource*, not *per property*

The question behind TD-31: when a Bicep/ARM deploy re-applies
`infra/modules/mcp/container-app.bicep`, does its `env` list **replace**
whatever is on the live Container App, or merge with it?

Microsoft's `deployment-modes` documentation addresses this directly, and
names the exact mistake:

> A common misunderstanding is to think properties that aren't specified in
> the template are left unchanged. If you don't specify certain properties,
> Resource Manager interprets the deployment as overwriting those values.
> Properties that aren't included in the template are reset to the default
> values.

and, on redeployment specifically:

> When redeploying an existing resource in incremental mode, all properties
> are reapplied. The properties aren't incrementally added.

The distinction that matters: **incremental mode is incremental at the
resource level** — resources absent from the template are left alone — **but
the body of a resource that *is* in the template is a full replacement.** The
template "always contains the final state of the resource. It can't represent
a partial update."

Applied to this repo, `infra/modules/mcp/container-app.bicep` L116:

```bicep
env: concat(envVars, keyVaultSecretEnv)
```

`env` is an array property whose value is computed entirely from template
inputs. On redeploy it is set to exactly that computed list. Any variable set
on the live app by any other means — portal, `az containerapp update`, a hand
edit — is **not** in `envVars`, is therefore not in the computed list, and is
therefore dropped.

And `MCP_WEB_LIVE_MODE` is not in `envVars`. Re-verified this session:

```
grep -rn "MCP_WEB_LIVE_MODE" --include=*.bicep --include=*.yml \
  --include=*.yaml --include=*.py --include=*.sh .
```

returns **only** function-package `tools.yaml` files describing the variable's
*effect*. It appears in no Bicep file, no workflow, and no script. It exists
solely as documentation of a variable nothing declares.

**TD-31's mechanism is confirmed.** The finding no longer rests on one
person's reading of an IaC template. What remains unverifiable from here is
the *current state* — whether the variable is set on `ca-mcp-web` right now —
which needs `az` access. So the correct phrasing of TD-31 is unchanged and
now properly grounded: *if* it is set, the next full infra deploy silently
removes it, and knowledge intake reverts to a synthetic fixture while
`caj-mcp-smoke` continues to pass.

### P4 — ✅ Confirmed: Key Vault has no Ed25519 key type, at any tier

The claim under test appears in three places in the codebase:

| Where | What it says |
|---|---|
| `services/publisher/app/verifier.py` L17–19 | "RS256 only. The contract also allows ES256/PS256/EdDSA, but EdDSA is unavailable on a standard-tier Key Vault (no Ed25519 key type at any SKU)" |
| `services/publisher/app/config.py` L7–8 | "See app/verifier.py for why RS256 and not EdDSA (this Key Vault SKU has no Ed25519 key type at all)" |
| `.compound/index.md` L-0031 | "Azure Key Vault standard tier cannot create/sign/verify EdDSA (Ed25519) keys — RSA (RS256/PS256) and EC P-256/P-384/P-521/P-256K (ES256/ES384/ES512) only" |

Microsoft's supported-curve list is exactly the four the codebase names:
**P-256, P-256K (SECP256K1), P-384, P-521**, alongside RSA. Ed25519 is absent.
Azure CLI rejects it explicitly — `az keyvault key create --curve Ed25519`
returns *"Unsupported curve: Ed25519. Supported curves are P-256, P-384,
P-521, P-256K, SECP256K1"* — and the corresponding `azure-cli` issue records
that this holds on a **premium** vault and on **Managed HSM** too, not only on
standard tier.

So the code's reasoning is correct, and slightly *understated*: this is not a
SKU limitation that a tier upgrade would lift. **L-0031 and TD-25's
remediation path are both validated**, and option (a) in
`docs/accepted-risks.md` — switch `signing.py` / `verify_signature.py` to
ES256 with the production key held in Key Vault — is the right default.

#### What P4 also surfaced

Two things worth recording, neither of which I expected going in.

**First, the platform runs two signing schemes, and the split is principled.**
Gate tokens are RS256 (`publisher/app/verifier.py`) because Key Vault holds
the key. The registry artefact is Ed25519 (`services/registry/signing.py`,
`SIGNATURE_ALGORITHM = "Ed25519"`, deterministic per RFC 8032) because the
build holds the key itself, in software, via `cryptography`. That is not
inconsistency — **the algorithm choice follows key custody**, which is the
correct way round. `14` should say so explicitly; at present it documents the
two schemes separately without naming the reason they differ.

**Second, a doc-drift item.** `services/registry/signing.py`'s module
docstring (L7–11) explains the `keyvault://` fail-loud path with only the
*networking* reason:

> Key Vault is not reachable from this scope (public network access is
> Disabled, no in-VNet runner)

But `docs/accepted-risks.md` carries the L-0031 correction added 2026-07-31,
which establishes a **second and harder** reason: even with a network path,
Key Vault could not hold this key at all, because the key is Ed25519. The
module docstring predates that correction and was not updated with it.

This matters because the docstring is what an engineer reads first, and it
implies the follow-up is blocked on infrastructure (get an in-VNet runner)
when it is actually blocked on an algorithm decision that requires a code
change. `accepted-risks.md` says this precisely — *"this is a config swap
only if option (a) is chosen; if the algorithm changes, `signing.py` /
`verify_signature.py` need the matching code change first"* — and the
docstring's own promise, *"moving to a production signing key is a
configuration swap, never a code change"*, is now known to be false for the
recommended path.

**Fix: three lines in the `signing.py` docstring**, pointing at L-0031. No
behaviour change. It is the cheapest item in this entire document set.

---

## Pending checks — identified, not yet run

Ordered by what they would change in this documentation set. P3 and P4 are
complete; the remainder are lower-value and unattempted.

| # | Question | Source to query | Would resolve |
|---|---|---|---|
| P5 | Do any Canva **brand templates** exist? | Canva `search-brand-templates`, `list-brand-kits` | Function 45's carousel path and `bulk_create_from_csv` are template-locked — `template_id` is required. No templates would be a hard blocker on the weekly loop that nothing in the repo records. |
| P6 | Do any Fireflies transcripts exist? | Fireflies `fireflies_get_transcripts` | Function 26 harvests advocacy from transcripts. The integration is unbuilt (`12` I18); if there is also no input, the function is inert on both sides. |
| P7 | Do the four ingestion URLs still resolve? | Microsoft Learn (for the Fabric page); web fetch for the RSS feeds | `fetch_sources.yaml`'s own header asks for exactly this: *"re-verify this liveness periodically, since a renamed/retired page silently narrows AC-24's guarantee rather than failing loudly."* |
| P8 | Is Semrush a realistic future signal source? | Semrush `domain_overview` | `contracts/vault-api.yaml`'s own example payload cites `source: "semrush"`, but no integration exists (`12` I18). |

P5 is now the most valuable of these: a missing brand template would be a hard
blocker on the weekly loop that nothing in the repo records.

---

## What this cannot fix

The connectors verify **counterparties**, not the platform. They cannot:

- inspect live Azure state (`cmos-dev` resource group, Container App revisions, actual env vars) — so **TD-31 cannot be confirmed as currently-broken or currently-fine**. P3 verified its *mechanism* against Microsoft's documentation; the *current state* of `ca-mcp-web` remains unknown from here;
- read the Postgres instance, so no claim about live data volumes or row states can be checked;
- read Application Insights, so no telemetry or trace claim can be checked;
- confirm whether `MCP_WEB_LIVE_MODE` is set on `ca-mcp-web` right now — the single most consequential open question in this documentation set.

Those all require `az` CLI access to the subscription. Until then, the
limitation stated in `README.md` stands unchanged.

## Operational note

Two constraints shaped how much of this log could be filled in, both worth
knowing before anyone tries to extend it:

1. **The MCP connectors flap.** Servers were observed disconnecting and
   reconnecting repeatedly, and alternating between friendly names and hashed
   ids. Any batch of checks should tolerate a mid-run drop and resume rather
   than assume a stable session.
2. **`learn.microsoft.com` is not directly reachable from this environment.**
   `WebFetch` and `curl` both return HTTP 403 through the proxy, and the
   Microsoft Learn MCP connector returned `requires approval` on every call.
   P3 and P4 were answered by search over the Learn corpus instead — good
   enough to settle both questions, but a weaker citation than reading the
   page, and labelled as such above.

## What was recorded elsewhere as a result

| From | Became |
|---|---|
| P3 | Confirmation paragraph added to `09` TD-31 |
| P4 | `09` TD-25 corrected — the limitation is **not** tier-specific |
| P4 side-finding | `09` TD-33 (new) — `signing.py` docstring predates L-0031 |
| V2 | `09` TD-32 (new, Priority 2) — brand rules uncalibrated against real output |
| V1 | No debt item. It is an adoption finding, not a defect — see `07` and `10` R1 |
