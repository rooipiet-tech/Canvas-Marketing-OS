# Regression fixtures

`42-linkedin-post-writer-broken/` is a copy of
`functions/42-linkedin-post-writer/` with **one deliberate defect**: the
roof-line rule has been deleted from `prompt.md`. Nothing else differs — the
golden eval tasks, `tool_check.py`, `schema.json` and `tools.yaml` are
byte-for-byte the shipped ones.

This proves the eval harness catches a *prompt* regression, not just a code
regression. Because each package's simulated output is derived from its own
`prompt.md`, deleting the rule removes the roof line from the generated post,
and exactly the two tasks that grade it fail by name:

```sh
python services/registry/eval_harness.py \
  --function services/registry/fixtures/regression/42-linkedin-post-writer-broken
# -> exit 1, naming lpw-001-roof-line-and-pillar and lpw-005-uncleared-client-not-named
```

If this fixture ever starts passing, the harness has stopped enforcing the
prompt, and every "evals passed" result elsewhere is worth less than it looks.
