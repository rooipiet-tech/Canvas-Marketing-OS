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

## Pending checks — identified, not yet run

Ordered by what they would change in this documentation set.

| # | Question | Source to query | Would resolve |
|---|---|---|---|
| P1 | Has this platform **ever** created a Buffer post? | Buffer `list_posts` | `00` and `09` state publishing is dry-run only, resting solely on `PUBLISHER_DRY_RUN` defaulting true. Zero posts would confirm it from the outside. |
| P2 | Is there any real Buffer performance data to ingest? | Buffer `get_aggregated_post_metrics` | `09` TD-11 says 3 of 4 analytics sources are fixtures. If Buffer has no post history, **all four** are effectively synthetic — a stronger and worse finding. |
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
