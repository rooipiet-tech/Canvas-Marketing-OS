# Function-definition registry

Tooling that turns the function-definition packages under `functions/` into a
**signed, versioned, tag-addressable, byte-identically reproducible**
artefact, and gates every change to them behind package validation, golden
evals, brand-safety checks and rubric linting.

Per C7 the artefact is deliberately a canonical-JSON manifest plus a detached
signature, **not** an OCI image: the four properties the goal actually
requires are all satisfied without a container registry that does not exist
in this environment.

## Layout

| Path | What it is |
|---|---|
| `common.py` | Shared helpers: repo-root resolution, canonical JSON, hashing, package discovery |
| `checks.py` | Generic rubric check kinds shared by every package |
| `gateway_client.py` | Model-gateway client; `build_mock_client` (default) vs `build_live_client` (`--live` only) |
| `signing.py` | Ed25519 signing/verification and the env-var-first key resolution order |
| `eval_task.schema.json` | Canonical golden-eval-task schema (invented here; no prior format existed) |
| `keys/` | The committed **development** signing keypair. Read `keys/README.md` first |
| `fixtures/` | Paired good/bad fixtures for every checker, plus the broken-prompt regression fixture |

## Scripts

Every script runs from the repo root as `python <script> [args]`, outside any
GitHub Actions context, and ends with a `PASS` line on stdout or a
`FAIL: <reason>` line on stderr, with a matching exit code.

```sh
pip install -r services/registry/requirements.txt

# Package shape, incl. tools.yaml vs contracts/function-definition/tools.schema.json
python services/registry/validate_package.py --all

# Build the artefact (twice, to any two directories, is byte-identical)
python services/registry/build_registry.py --out dist/ --sign
python services/registry/build_registry.py --resolve-tag v1.0.0
python services/registry/verify_signature.py --artefact-dir dist/

# Golden evals against the mocked gateway (no network, ever)
python services/registry/eval_harness.py --all
python services/registry/eval_harness.py --function functions/42-linkedin-post-writer

# Wired-but-deferred live path: SKIPPED per function when ANTHROPIC_API_KEY is unset
python services/registry/eval_harness.py --all --live

# Deterministic brand safety and rubric gradeability
python services/registry/safety_suite.py --dir services/registry/fixtures/safety/clean
python services/registry/lint_rubrics.py --all
```

Self-checking scripts (each asserts a property rather than just running):

```sh
python services/registry/test_key_resolution.py    # env-var key beats the dev-key fallback
python services/registry/test_gateway_contract.py  # contract conformance + zero live calls
python services/registry/test_live_path.py         # skip path vs attempt path
python services/registry/test_safety_suite.py      # each fixture's exact violation codes
python functions/02-brand-steward-qa/permission_check.py   # default-deny self-test
```

## Two things worth knowing before you change anything

**The mocked gateway is driven by each package's own `prompt.md`.** A
package's `tool_check.py` derives its simulated output from the rules stated
in its prompt. Delete a rule from a prompt and the tasks grading that rule
fail. That is what makes `fixtures/regression/42-linkedin-post-writer-broken/`
— a copy of function 42 with the roof-line rule removed — fail by task id.
Without that coupling, "evals passed" would only ever mean "the code still
runs".

**Client naming is default-deny.** `docs/permission-register.yaml` is the
interim register (it graduates to the Vault `consent_register` table). A name
absent from that file blocks in exactly the same way as one explicitly marked
`UNCLEARED` — absence is never permission. Nothing is `CLEARED` today.

## Relationship to `functions/task-worker/`

`functions/` now holds two kinds of thing, as siblings: the Azure Function App
scaffold (`task-worker/`, which per SCOPE-001 owns no business logic) and
function-definition **content** packages (`02-*`, `09-*`, `42-*`). Package
discovery keys on the `contracts/function-definition/TEMPLATE/` file shape, so
`task-worker/` is skipped rather than rejected.
