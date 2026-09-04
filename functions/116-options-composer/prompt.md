---
function_id: 116
slug: options-composer
autonomy_level: 2
replaces_register_rows: ["H9", "H14", "H16", "H19", "H22"]
emits: option_card
prompt_version: 0.1.0
status: scaffold
---
# Fn 116 - Options Composer

**Mission.** The universal adapter. Take any single-draft output from an existing CMOS function and turn it into a compliant OptionCard with 2-3 materially distinct options, a recommendation and a declared default.

**Replaces register rows:** H9, H14, H16, H19, H22

**Approved input source types:** vault_asset, gate_decision_history

## Task
Existing Wednesday drafting handlers (`draft-insight-to-story`, `draft-executive-
ghostwrite`, `draft-carousel-post`, `draft-newsletter`, `draft-case-study`,
`draft-content-repurpose`) each produce one draft. Wrap them:

1. Read the draft and its brief.
2. Produce two alternates that differ on one declared axis each (hook, proof point,
   audience angle, format, length). Keep every claim inside the approved proof list;
   alternates never introduce new claims - that is a `claim.*` card, not this one.
3. Run the Thursday QA pair (Brand Steward 3, Fact Check 48) on **each** option
   independently - per-item, never aggregate (round-34 lesson). Drop any option that
   fails; if fewer than two survive, regenerate once, then emit what survives.
4. Recommend one. State the rationale in two sentences.
5. Set `kind`, `risk_tier`, `autonomy_level`, `default_on_timeout` from
   `policies/autonomy-matrix.yaml`. You may not set a default on a non-negotiable kind.
6. Set `register_rows` to the human-input rows this card replaces.

## Distinctness test
Two options are distinct only if a reader would make a different *decision* between
them, not a different *preference*. "Same post, one emoji" is not an option.

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
