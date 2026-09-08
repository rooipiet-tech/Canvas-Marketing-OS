# Function register coverage

*Originally written for session/s10-intelligence, reflecting `main` after
PR#6 (`session/s6-registry`) and PR#7 (`session/s3-orchestrator`). Updated
7 Sep 2026 (TD-18): `registry.yml`'s `validate_package.py` and
`lint_rubrics.py` steps now call `--all` instead of hardcoding three package
paths, so every row below is exercised in CI, not just 02/09/42.
`eval_harness.py --all` was already generalized before this change.*

## Coverage table

| Function id | Name | Package path | Status | Eval count |
|---|---|---|---|---|
| 02 | Brand Steward QA | `functions/02-brand-steward-qa` | live | 6 |
| 09 | Market Intelligence Director | `functions/09-market-intelligence-director` | live | 5 |
| 10 | Competitor Discovery Scanner | `functions/10-competitor-discovery-scanner` | live | 5 |
| 11 | Competitor Change Monitor | `functions/11-competitor-change-monitor` | live | 6 |
| 12 | Competitive Positioning Analyst | `functions/12-competitive-positioning-analyst` | live | 5 |
| 13 | Competitor Content Performance Scout | `functions/13-competitor-content-performance-scout` | live | 6 |
| 16 | Microsoft Fabric Ecosystem Scout | `functions/16-microsoft-fabric-ecosystem-scout` | live | 5 |
| 17 | Source Scout | `functions/17-source-scout` | live | 5 |
| 18-01 | Vertical Intelligence - Logistics & Fleet/Telematics | `functions/18-01-vertical-intel-logistics-fleet` | live | 5 |
| 18-02 | Vertical Intelligence - Mining & Industrial | `functions/18-02-vertical-intel-mining-industrial` | live | 5 |
| 18-03 | Vertical Intelligence - Manufacturing (proof-light) | `functions/18-03-vertical-intel-manufacturing` | live | 6 |
| 18-04 | Vertical Intelligence - Construction | `functions/18-04-vertical-intel-construction` | live | 5 |
| 18-05 | Vertical Intelligence - FMCG & Beverage | `functions/18-05-vertical-intel-fmcg-beverage` | live | 5 |
| 18-06 | Vertical Intelligence - Financial Services | `functions/18-06-vertical-intel-financial-services` | live | 5 |
| 25 | Competitive Response Strategist | `functions/25-competitive-response-strategist` | live | 6 |
| 26 | Client Advocacy Harvester | `functions/26-client-advocacy-harvester` | live | 6 |
| 39 | Insight-to-Story Editor | `functions/39-insight-to-story-editor` | live | 6 |
| 41 | Research Brief Writer | `functions/41-research-brief-writer` | live | 5 |
| 42 | LinkedIn Post Writer | `functions/42-linkedin-post-writer` | live | 5 |
| 43 | Executive/Founder Ghostwriter | `functions/43-executive-ghostwriter` | live | 5 |
| 45 | Carousel/Document Post Writer | `functions/45-carousel-post-writer` | live | 6 |
| 46 | Email/Newsletter Writer | `functions/46-newsletter-writer` | live | 5 |
| 47 | Case Study Writer | `functions/47-case-study-writer` | live | 6 |
| 48 | Fact-Check Verdict | `functions/48-fact-check-verdict` | live | 8 |
| 52 | Content Repurposer | `functions/52-content-repurposer` | live | 6 |
| 128 | Source Discovery & Lifecycle Manager | `functions/128-source-discovery-lifecycle` | scaffold | 5 |
| 129 | Web Reach Governor | `functions/129-web-reach-governor` | scaffold | 5 |

All 27 rows are discovered by `services/registry/common.py`'s
`discover_function_packages()` — the same helper `validate_package.py`,
`lint_rubrics.py` and `eval_harness.py` all call with `--all` — because each
has the full TEMPLATE package shape (`prompt.md`, `skill.md`, `tools.yaml`,
`schema.json`, `evals/`). `python services/registry/eval_harness.py --all`
passes 148/148 golden eval tasks across these 27 packages;
`python services/registry/lint_rubrics.py --all` lints all 440 rubric
entries in those 148 task files clean; `python
services/registry/validate_package.py --all` validates all 27 package
shapes. Functions 128 and 129 carry `status: scaffold` in their own
`prompt.md` frontmatter (pre-existing, unrelated to this change) but still
ship a full package shape and golden evals, so they are correctly included.

`functions/113-expertise-corpus-miner` through
`functions/127-eval-generator` (15 directories) are **not** counted here:
each is missing `skill.md`/`tools.yaml`/`evals/` (and 123 is also missing
`schema.json`), so `is_function_package()` correctly skips them as
not-yet-built stubs rather than shipped packages.

## Notes

- Function 18 (vertical intelligence) is implemented as six independent
  packages (`18-01` through `18-06`), never a single monolithic
  `functions/18-vertical-intelligence-network` package. See
  `functions/_shared/vertical-intelligence-method.md` for the method they
  share.
- Function 18-03 (Manufacturing) is deliberately proof-light: `docs/positioning.md`
  section 4 names five vertical proof areas and Manufacturing is not one of
  them. Its evals default `evidence_grade` to `light`, never `strong` - see
  `functions/18-03-vertical-intel-manufacturing/prompt.md`'s "Proof-light
  default" section.
- Function 25 consumes the cards functions 10, 11, 12, 13, 16 and 18-01..18-06
  produce and ranks them into a severity-scored response plan, naming the
  `RIB BI+ move` and `BuildSmart-native-BI move` playbook templates. See
  `docs/brief-rollup-and-followups.md` for how this feeds the daily brief.
- Function 128 (per its own `prompt.md`) absorbs `functions/17-source-scout`;
  17 remains present and counted above as its own package until that
  migration lands, at which point this table should drop the 17 row rather
  than leave a stale duplicate.
- **Resolved (TD-18, 7 Sep 2026):** `registry.yml`'s CI wiring previously
  hardcoded the 3 pre-existing package paths (02/09/42) for
  `validate_package.py` and `lint_rubrics.py` (and never picked up the 24
  packages added since). Both steps now call `--all`, reusing
  `discover_function_packages()` exactly as `eval_harness.py --all` already
  did. Generalizing `lint_rubrics.py` surfaced 12 real, pre-existing rubric
  gradeability violations in `functions/17-source-scout` and
  `functions/129-web-reach-governor` (rubric conditions with no observable
  anchor, one also under the minimum length) — never caught before because
  CI never linted those packages. Fixed alongside this change.
