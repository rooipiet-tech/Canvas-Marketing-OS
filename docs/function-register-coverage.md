# Function register coverage

*Written for session/s10-intelligence. Reflects the state of `main` after
PR#6 (`session/s6-registry`, merged 2026-07-31T07:59Z) and PR#7
(`session/s3-orchestrator`, merged 2026-07-31T08:21Z), both merged before
this branch was rebased and this build started (spec_version 3, amendment
v3). Functions 02, 09 and 42 are therefore genuinely present on this branch
today - not "pending merge" - and are marked `live` honestly below.

## Coverage table

| Function id | Name | Package path | Status | Eval count |
|---|---|---|---|---|
| 02 | Brand Steward QA | `functions/02-brand-steward-qa` | live | 5 |
| 09 | Market Intelligence Director | `functions/09-market-intelligence-director` | live | 5 |
| 10 | Competitor Discovery Scanner | `functions/10-competitor-discovery-scanner` | live | 5 |
| 11 | Competitor Change Monitor | `functions/11-competitor-change-monitor` | live | 5 |
| 12 | Competitive Positioning Analyst | `functions/12-competitive-positioning-analyst` | live | 5 |
| 13 | Competitor Content Performance Scout | `functions/13-competitor-content-performance-scout` | live | 5 |
| 16 | Microsoft Fabric Ecosystem Scout | `functions/16-microsoft-fabric-ecosystem-scout` | live | 5 |
| 18-01 | Vertical Intelligence - Logistics & Fleet/Telematics | `functions/18-01-vertical-intel-logistics-fleet` | live | 5 |
| 18-02 | Vertical Intelligence - Mining & Industrial | `functions/18-02-vertical-intel-mining-industrial` | live | 5 |
| 18-03 | Vertical Intelligence - Manufacturing (proof-light) | `functions/18-03-vertical-intel-manufacturing` | live | 6 |
| 18-04 | Vertical Intelligence - Construction & BuildSmart | `functions/18-04-vertical-intel-construction-buildsmart` | live | 5 |
| 18-05 | Vertical Intelligence - FMCG & Beverage | `functions/18-05-vertical-intel-fmcg-beverage` | live | 5 |
| 18-06 | Vertical Intelligence - Financial Services | `functions/18-06-vertical-intel-financial-services` | live | 5 |
| 25 | Competitive Response Strategist | `functions/25-competitive-response-strategist` | live | 6 |
| 42 | LinkedIn Post Writer | `functions/42-linkedin-post-writer` | live | 5 |

All 15 rows are marked `live`: every package listed exists on this branch's
working tree right now, with a full `prompt.md` / `skill.md` / `tools.yaml`
/ `schema.json` / `tool_check.py` / `evals/` shape, and passes
`python services/registry/eval_harness.py --all` and
`python services/registry/safety_suite.py --dir <package>` directly against
this repo - no temp-worktree scaffolding, no reachability caveat.

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
- `registry.yml`'s CI wiring (`validate_package.py`, `lint_rubrics.py`)
  hardcodes the 3 pre-existing package paths (02/09/42) and does not yet
  auto-discover the 12 new packages added in this build - see
  `docs/brief-rollup-and-followups.md`'s third follow-up for why that is out
  of this build's touch-scope.
