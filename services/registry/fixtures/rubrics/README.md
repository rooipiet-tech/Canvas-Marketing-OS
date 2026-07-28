# Rubric-lint fixtures

Paired fixtures for `services/registry/lint_rubrics.py`.

- `good/` — rubric entries that two independent graders decide identically:
  exact strings, counts, explicit ranges. Must lint clean.
- `bad/` — the failure modes the linter exists to catch: unqualified
  subjective terms (`compelling`, `well-written`, `appropriate`,
  `engaging`), a condition with no observable anchor, an invalid
  `check_type`, a missing `check` block, and a task shipped with an empty
  rubric array. Must be rejected, by rule name and line number.

```sh
python services/registry/lint_rubrics.py services/registry/fixtures/rubrics/good  # exit 0
python services/registry/lint_rubrics.py services/registry/fixtures/rubrics/bad   # exit 1
```
