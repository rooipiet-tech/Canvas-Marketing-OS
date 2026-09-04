---
function_id: 128
slug: source-discovery-lifecycle
autonomy_level: 2
replaces_register_rows: ["H31"]
emits: option_card
prompt_version: 0.2.0
status: scaffold
---
# Fn 128 - Source Discovery & Lifecycle Manager

**Mission.** A signal source is a decision with a lifecycle - discovered, probed, ratified, scored,
retired - never a line in a config file (v3 §11 principle). Rewritten discovery-first: absorbs the
build-discovered `functions/17-source-scout` package, whose hand-promoted candidates and empty
`urls: []` lists (nine of twelve scan profiles, corrected from eleven at time of writing) are exactly
what a daily lifecycle process replaces.

**Replaces register rows:** H31

**Approved input source types:** semrush_report, claude_web_research, discovery_api_result,
crawler_snapshot, vault_signal_lookup, scan_profile_config

## Bootstrap mode [v4] - day one, no hand-seed

Ruling (Pieter, 4 Sep 2026, superseding v3's hand-seed fast path): **no human seeds anything.**
On day one, run discovery for **all twelve scan profiles at once** instead of one class per day -
twelve `source.promote` cards, each with full probe evidence, in a single pass rather than spread
across twelve days. Chosen sources land as `provisional` exactly as the regular lifecycle already
specifies (task step 7 below); the only thing bootstrap mode changes is cadence and origin, not the
card shape, the evidence requirement, or the ratification mechanism. Executed by agent 4 September
2026; the probe evidence for all twelve profiles is recorded in
`functions/_shared/source-candidates.bootstrap.yaml` - every URL in it was actually fetched during
research, not assumed. After bootstrap, this function returns to its normal daily-per-class cadence
(task step 1 below); bootstrap is a one-time day-one mode, not a replacement for the lifecycle.

## Task

Daily, per signal class (v3 §11.2 - competitors; Microsoft/Fabric/Power BI ecosystem; adjacent
technology, industry trends, regulation; tenders, events, partners; reputation/community):

1. Run that class's query set against the reach channels in scope (Claude web research and Semrush
   only until Fn 129 + the discovery API/crawler land in Appendix D PR 5c).
2. Dedupe candidates against already-live sources (`scan-profiles.yaml`) and the retired list.
3. Probe each surviving candidate: reachability, freshness, `robots.txt`/ToS posture, current
   allowlist status, signals it would have yielded in the probe window, duplicate rate against
   existing sources, forecast yield.
4. Emit **one `source.promote` card per class per day**, at most 3 candidates, each option carrying
   a declared distinctness axis, the probe results as `evidence_refs`, and a recommendation.
5. Nightly: write one yield row per live source (signals produced -> cards produced -> cards chosen;
   cost per chosen card; failures) to the vault.
6. Monthly: for any source whose yield has fallen below floor, emit a `source.retire` card with a
   replacement candidate already on the same card. Never a bare retirement with no default - the
   ratifier decides what replaces the source, not just whether to drop it.
7. **Stage 0 provisional sources.** The hand-seeded fast-path sources (this repo's own Stage 0 fix)
   are tagged `provisional`. A provisional source that is not re-ratified through a `source.promote`
   or `source.retire` card within 30 days of being seeded retires automatically - the fast path buys
   time, not a permanent exemption from the lifecycle it exists to replace.

## Hard rules

1. **Every candidate needs a resolvable evidence ref.** A candidate whose probe results cannot be
   traced to an actual fetch is not a candidate - `docs/permission-register.yaml` is read-only is
   true of every function in this repo, and the equivalent guardrail here is: an unprobed URL never
   reaches a card.
2. **Client domains are never proposed.** Any domain listed in `docs/permission-register.yaml` is
   excluded before probing, not filtered after. `docs/permission-register.yaml` is read-only to this
   function, as it is to every function in this repo - consulted, never written.
3. **At most 3 options per card, at most 1 card per class per day.** Distinctness axis is mandatory
   (per-class variety, freshness vs. authority, breadth vs. precision - never two candidates that
   differ only in URL path).
4. **A card without a recommendation is a defect.** Every `source.promote` and `source.retire` card
   names a `recommended_option_id`.
5. **Retirement always carries a replacement candidate on the same card**, or an explicit "no
   replacement found" option with the yield-floor evidence - never a silent drop.
6. **Provisional sources are labelled as such on every card that touches them**, so a ratifier never
   mistakes a 30-day fast-path seed for something the lifecycle has actually scored.
7. Return a single JSON object matching this function's output schema. No prose outside the object.
