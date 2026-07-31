# Content Studio function coverage

Hand-authored coverage doc for the eight content-studio function-definition
packages shipped in this build. This is a **stand-in for
`services/registry`'s `registry.json`** until that tooling is extended to
include these functions — read this file, not a registry index, for the
current live set. Every row below names the function's exact on-disk
package path so this doc can never silently drift from `functions/`.

All eight packages validate against
`contracts/function-definition/tools.schema.json` and
`services/registry/eval_task.schema.json` (`python
services/registry/validate_package.py --all --min-eval-tasks 5`), and pass
their full golden eval set under the mocked gateway (`python
services/registry/eval_harness.py --all`).

## Live functions

| # | Package path | Purpose | Gate-check `function_id`(s) |
|---|---|---|---|
| 26 | `functions/26-client-advocacy-harvester` | Turns an approved Fireflies transcript excerpt into a client-advocacy/testimonial intake record, gated on a local consent-register fixture and the client-naming permission register. | `draft.brief` |
| 39 | `functions/39-insight-to-story-editor` | Turns a raw insight (typically function 41's brief) into a narrative story draft closing on the roof line, pillar-tagged and CTA-complete. | `draft.social_post`, `publish.social_post` |
| 41 | `functions/41-research-brief-writer` | Turns a signal or opportunity card into a structured, cited research brief that every Wednesday drafting function works from. | `draft.brief` (this function's own stage); `publish.social_post` is a downstream-only reference — never emitted by function 41 itself |
| 43 | `functions/43-executive-ghostwriter` | Ghostwrites first-person opinion content in a named executive's voice, never fabricating an opinion the executive did not actually state. | `draft.social_post` |
| 45 | `functions/45-carousel-post-writer` | Writes a multi-slide LinkedIn carousel/document post and produces its Canva Bulk Create CSV manifest, fixture-first with no live mcp-canva call. | `draft.social_post`, `publish.social_post` |
| 46 | `functions/46-newsletter-writer` | Writes the long-form owned-channel newsletter/email digest of the week's proof points. | `draft.social_post` (drafting), `publish.blog_article` (publishing — closest existing autonomy.yaml analogue, no email-specific identifier exists) |
| 47 | `functions/47-case-study-writer` | Turns a proof point into a public case-study draft in a situation/approach/result structure, naming a client only if CLEARED. Intentionally excluded from the Friday auto-schedule step; published on a separate, human-driven cadence once a client is CLEARED (mirroring function 43's own exclusion). | `draft.social_post`; `publish.social_post` is a downstream-only reference — never auto-invoked by this loop |
| 52 | `functions/52-content-repurposer` | Repurposes one existing long-form asset (typically function 46 or function 47's output) into 2-3 shorter derivative social formats. | `draft.social_post`, `publish.social_post` |

## Notes

- **Client naming**: every function above defaults to client-free output.
  `docs/permission-register.yaml` is the single source of truth for
  clearance; nothing is CLEARED today, so absence from the register blocks
  identically to an explicit `UNCLEARED` entry (default deny). Functions 26
  and 47 additionally ship a full `permission_check.py` module exercising
  that default-deny path directly, rather than a hard-coded assumption.
- **Gate-check identifiers**: every `function_id` above is one of the four
  reused `services/gatekeeper/policy/autonomy.yaml` pairs —
  `publish.social_post`, `publish.blog_article`, `draft.social_post`,
  `draft.brief` — never an invented identifier, which would fail-closed
  forever.
- **Weekly orchestration**: all eight functions are wired into
  `services/orchestrator/loops/weekly-content-loop.yaml`'s Monday-through-
  Friday task graph — Monday planning, Tuesday research and advocacy
  harvest, Wednesday drafting (all six drafting functions in parallel),
  Thursday dual-verdict QA (Brand Steward + fact-check), Friday scheduling
  and publication gated on both Thursday verdicts.
- **Canva/Buffer/Fireflies/model-gateway**: every integration these
  functions reference is fixture-first or routes through the documented
  Gatekeeper gate-check; none claims or implies POPIA or full
  data-residency compliance.
