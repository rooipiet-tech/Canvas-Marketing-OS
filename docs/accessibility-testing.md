# Console accessibility testing (GOAL-005 / GOAL-005b)

## Tooling availability (probed per L-0001)

This session probed `npx --yes lighthouse@latest --version` and
`npx --yes @axe-core/cli --version` before relying on either as a build
gate — both succeeded (lighthouse 13.4.1, @axe-core/cli 4.12.1).
GitHub-hosted `ubuntu-latest` runners ship Google Chrome pre-installed, so
`.github/workflows/ci.yml`'s `console-accessibility` job runs both tools
automatically, ungated, on every push — no manual substitute step is
needed.

## What the CI job does

1. Installs `services/telemetry-lib` + `console`.
2. Starts the console locally with `VAULT_API_MODE=mock` /
   `GATEKEEPER_API_MODE=mock`, seeded via
   `console/tests/seed_fixtures.py` — the exact same
   `CONSOLE_SEED_FIXTURES_JSON_B64` mechanism
   `.github/workflows/deploy-console.yml`'s gated smoke job uses against
   the real deployed console (see that file's `(c)` step), so the local
   accessibility run exercises realistic seeded data, not an empty state.
3. Runs Lighthouse (`--only-categories=accessibility`) against `/tasks`,
   `/approvals`, `/costs` — the 3 Must screens — and asserts each scores
   `>=0.90` (GOAL-005).
4. Runs `@axe-core/cli` against `/vault-search` and `/kill-switch` — the 2
   Should screens — and asserts 0 critical/serious violations (GOAL-005b).

## Manually verified this session (builder, local sandbox)

Before wiring the CI job, the builder ran a real local `uvicorn` instance
(seeded the same way) and Lighthouse with real headless Chrome directly
against it:

| Screen | Accessibility score |
|---|---|
| `/tasks` | 1.0 |
| `/approvals` | 1.0 |
| `/costs` | 1.0 |

`@axe-core/cli`'s CLI run hit a local Chrome/ChromeDriver binary-version
mismatch specific to this sandbox (not a page-markup defect — Lighthouse,
which drives Chrome directly rather than through a separately-versioned
ChromeDriver binary, worked without issue against the same pages) that
was not fully resolved locally; this is flagged explicitly as a
self-flag rather than asserted as verified. GitHub's `ubuntu-latest`
images keep Chrome and ChromeDriver in sync, so the CI job is expected to
succeed there, but that was not empirically confirmed this session.

## No app-level auth on GET routes locally

Container Apps Easy Auth is a platform-level ingress feature, not
reproducible by a bare local `uvicorn` process. The console's own FastAPI
routes only check authentication on the one write route
(`POST /kill-switch/toggle`, via `app/auth.py`'s
`principal_from_headers`) — every GET route is unauthenticated at the
app layer by design (auth enforcement is delegated entirely to the
platform in production). The CI job's Lighthouse invocation still passes
an `X-MS-CLIENT-PRINCIPAL-*` extra-header pair for documentation/parity
with a real authenticated session, even though it is not load-bearing for
these specific GET routes locally.
