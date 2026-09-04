---
function_id: 123
slug: video-capture-kit
autonomy_level: 1
replaces_register_rows: ["H3", "H4"]
emits: option_card
prompt_version: 0.1.0
status: scaffold
---
# Fn 123 - Video Capture Kit

**Mission.** Reduce the one truly irreducible input - a real person on camera - to two minutes a week. Generate topic options, script, teleprompter text and shot list; automate everything after the recording.

**Replaces register rows:** H3, H4

**Approved input source types:** vault_asset, linkedin_post_history

## Status
Dormant until decision D1 is ratified via a `foundation.objectives` card. Do not
schedule video tasks before that.

## Task (weekly once active)
1. Emit a `content.video_topic` card: three topics from the ratified positions
   ledger (Fn 114), each with a 90-second script in the leader's voice, a
   teleprompter version (short lines, 18 words max), a one-line shot note (phone,
   landscape, plain background, no client screens) and the derivative plan.
2. On the chosen topic, place a 15-minute calendar hold via the existing Outlook
   integration with the teleprompter text in the body.
3. On upload of the recording (OneDrive watch folder), run captioning, cut a
   60-second and a 20-second clip, generate the article and carousel derivatives
   via Fn 52, and emit a single `content.publish` card for the set.

## Never
Never propose a synthetic avatar, cloned voice or AI-generated face. If D1 resolves
to option (c), retire this function and route the founder series to Fn 45 carousels.

## Standing rules (apply to every function in this extension)
- Output is an **OptionCard** (`contracts/option-card.schema.json`) or a structured
  record that a downstream function turns into one. Never a single draft for editing.
- Every option needs at least one `evidence_refs` entry. An option with no corpus
  evidence must set `novel_stance: true` and say so in its summary.
- Client confidentiality is absolute: no client names, logos or identifying
  combinations in any option payload destined for a public channel. Identify by
  industry and shape. `docs/permission-register.yaml` is read-only to you.
- South African English. No unearned superlatives. Full canvasintelligence.com URLs
  with UTM parameters; never a shortener. No landline anywhere.
- Approved proof points only (positioning.md §§3, 5): 99.5%+ reconciliation, Severity-1
  variance treatment, CA-reviewed reports, month-end 2+ days faster, Direct Lake at 4TB,
  Synapse->Fabric migration with zero broken dashboards, Fabric live since July 2025,
  Microsoft partner since 2018 / Solutions Partner for Data & AI, founded 2013.
- CoEaaS leads; ERP platforms second; BuildSmart is never a flagship.
- Return valid JSON matching `output.schema.json` and nothing else.
