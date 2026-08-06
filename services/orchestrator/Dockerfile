# Canvas Marketing OS — orchestrator service image
#
# Pinned patch tag (never :latest for the base image, F7). Non-root
# runtime user: dependencies are installed as root (needed for
# system-level installs), then USER app is set BEFORE the final CMD, so
# the process itself never runs as root. COPY only — no ADD from a URL.
#
# Multi-stage build (RISK-002): requirements-test.txt (pytest/
# pytest-asyncio/freezegun) and the tests/ directory are only needed to run
# this repo's test suite — that happens in CI directly on the runner (see
# .github/workflows/ci.yml's orchestrator-test job), never inside this
# image. The builder stage installs only the orchestrator package itself
# into a self-contained --prefix; that prefix plus the application source
# are copied into the final runtime stage, so pytest and friends are never
# even installed anywhere this image's layers.

FROM python:3.12-slim-bookworm AS builder

WORKDIR /app

COPY pyproject.toml ./
COPY main.py ./
COPY orchestrator ./orchestrator
COPY loops ./loops
COPY migrations ./migrations
COPY scripts ./scripts

# INCIDENT (2026-08-01, live — caj-loop-e2e-smoke #8 attempt 2): every
# dispatched task failed with `ModuleNotFoundError: No module named
# 'telemetry_lib'` the instant it reached dispatch.py's top-level `from
# telemetry_lib import set_span_attribute` (dispatch.py line 40) — /health
# and GET /runs/{task_ref} kept responding 200 because neither route
# imports dispatch.py, so the run just silently never advanced past 0/7
# stages terminal instead of crashing loudly. Root cause: telemetry-lib is
# a sibling package (services/telemetry-lib), not published to any index —
# this image never installed it, despite pyproject.toml's own header
# comment describing a "separate `pip install -e services/telemetry-lib`
# step" that mirrors console/Dockerfile's convention; that step was never
# actually added here. .github/workflows/orchestrator-image.yml's "Stage
# telemetry-lib into the build context" step copies the sibling package in
# — the same mechanism already used for contracts/, functions/, and docs/
# below, all of which live outside this image's services/orchestrator
# build context for the identical reason.
COPY telemetry-lib ./telemetry-lib

RUN pip install --no-cache-dir --prefix=/install ./telemetry-lib -e .

FROM python:3.12-slim-bookworm

WORKDIR /app

RUN useradd --create-home --shell /bin/bash app

COPY --from=builder /install /usr/local
COPY --from=builder /app/main.py ./
COPY --from=builder /app/orchestrator ./orchestrator
COPY --from=builder /app/loops ./loops
COPY --from=builder /app/migrations ./migrations
COPY --from=builder /app/scripts ./scripts

# INCIDENT (2026-07-31): confirmed live via ca-orchestrator's own startup
# logs (`loop_load_failed ... No such file or directory:
# '/contracts/orchestrator/loop-definition.schema.json'`) and
# caj-orchestrator-smoke-test crashing before it could publish anything.
# The frozen contract files loop_loader.py (every shipped loop, validated
# on startup and by the smoke test's own prediction path) and producer.py
# (the service-bus/orchestrator envelope schemas every outgoing message is
# validated against before publish, AC-026) read at RUNTIME live at the
# repository root, in contracts/ — outside this image's build context
# (services/orchestrator), which Docker's COPY cannot reach.
# .github/workflows/orchestrator-image.yml's "Stage the frozen contracts
# into the build context" step copies them into ./contracts/ here,
# immediately before `docker build` runs. Written out explicitly (not
# folded into a wildcard COPY) so a build without that staging step FAILS
# LOUDLY at build time instead of producing an image that only breaks on
# its first loop load / publish.
COPY contracts/orchestrator/loop-definition.schema.json ./contracts/orchestrator/loop-definition.schema.json
COPY contracts/orchestrator/heartbeat-event.schema.json ./contracts/orchestrator/heartbeat-event.schema.json
COPY contracts/orchestrator/dead-letter-alert.schema.json ./contracts/orchestrator/dead-letter-alert.schema.json
COPY contracts/service-bus/task-envelope.schema.json ./contracts/service-bus/task-envelope.schema.json

# Points config.contracts_dir() at the staged copies above. Without it the
# app falls back to walking parent directories from its own source file
# looking for a repository checkout, which does not exist inside this
# image — see orchestrator/config.py's contracts_dir() header for the full
# incident writeup.
ENV CONTRACTS_DIR=/app/contracts

# SAME incident class, caught while reconciling this session's own
# dispatch.py against the fix above (plan step 25's rebase reconciliation
# pass, test_contracts_path_resolution.py's module-level-parent-walk sweep
# test): dispatch.py's ingest-signals/qa-review/draft-content handlers read
# a prompt.md, a permission_check.py, and a fetch_sources.yaml from
# functions/ at RUNTIME — also outside this image's build context, for the
# identical reason contracts/ is. .github/workflows/orchestrator-image.yml's
# "Stage the frozen functions into the build context" step copies just the
# files these handlers actually read into ./functions/ here, immediately
# before `docker build` runs — never the rest of any function package
# (evals/, tool_check.py, skill.md, etc. stay out, same RISK-002
# minimal-image intent as contracts/ above).
#
# EXTENDED (6 Aug 2026, weekly-content-loop dispatch handlers): the same
# gap this comment already warns about bit again immediately -- PR #84
# added 8 new _read_prompt() call sites (functions 26, 39, 41, 43, 45, 46,
# 47, 52) plus a brand-new function package (48-fact-check-verdict) and
# none of the 9 were added here, so the built image would have shipped
# without them and every one of those handlers (everything except the
# deterministic plan-content-monday_handler) would have raised
# FileNotFoundError the first time weekly-content-loop actually dispatched
# a task -- caught before that image ever reached ca-orchestrator, not
# after. Only prompt.md is staged for each: dispatch.py's _read_prompt()
# never reads anything else from these 9 packages (no fetch_sources.yaml,
# no permission_check.py -- client-naming clearance stays centralised in
# function 02's copy, already staged below).
COPY functions/09-market-intelligence-director/prompt.md ./functions/09-market-intelligence-director/prompt.md
COPY functions/09-market-intelligence-director/fetch_sources.yaml ./functions/09-market-intelligence-director/fetch_sources.yaml
COPY functions/42-linkedin-post-writer/prompt.md ./functions/42-linkedin-post-writer/prompt.md
COPY functions/02-brand-steward-qa/prompt.md ./functions/02-brand-steward-qa/prompt.md
COPY functions/02-brand-steward-qa/permission_check.py ./functions/02-brand-steward-qa/permission_check.py
COPY functions/26-client-advocacy-harvester/prompt.md ./functions/26-client-advocacy-harvester/prompt.md
COPY functions/39-insight-to-story-editor/prompt.md ./functions/39-insight-to-story-editor/prompt.md
COPY functions/41-research-brief-writer/prompt.md ./functions/41-research-brief-writer/prompt.md
COPY functions/43-executive-ghostwriter/prompt.md ./functions/43-executive-ghostwriter/prompt.md
COPY functions/45-carousel-post-writer/prompt.md ./functions/45-carousel-post-writer/prompt.md
COPY functions/46-newsletter-writer/prompt.md ./functions/46-newsletter-writer/prompt.md
COPY functions/47-case-study-writer/prompt.md ./functions/47-case-study-writer/prompt.md
COPY functions/48-fact-check-verdict/prompt.md ./functions/48-fact-check-verdict/prompt.md
COPY functions/52-content-repurposer/prompt.md ./functions/52-content-repurposer/prompt.md

# Points config.functions_dir() at the staged copies above -- same
# rationale as CONTRACTS_DIR; see orchestrator/config.py's functions_dir()
# header for the full incident writeup.
ENV FUNCTIONS_DIR=/app/functions

# SAME incident class again (output-reviewer F2, major, same fix pattern
# as CONTRACTS_DIR/FUNCTIONS_DIR above): permission_check.py's
# find_repo_root() walks up looking for pyproject.toml to locate
# docs/permission-register.yaml -- there is no repository checkout inside
# this image, so that walk would raise RuntimeError the first time
# client_references is ever non-empty (dormant today only because
# dispatch.py hardcodes client_references=[]). Stage just the one file
# permission_check.py actually reads, never the rest of docs/.
COPY docs/permission-register.yaml ./docs/permission-register.yaml

# Points permission_check.py's register_path() at the staged copy above --
# see functions/02-brand-steward-qa/permission_check.py's register_path()
# header for the full incident writeup.
ENV PERMISSION_REGISTER_PATH=/app/docs/permission-register.yaml

# caj-orchestrator-smoke-test (infra/modules/orchestrator/smoke-test-job.bicep)
# runs `python -m orchestrator.smoke_test` from THIS SAME image — it
# compares the live app's /status output against this one static golden
# fixture. Shipping just this one JSON file (never the rest of tests/ — no
# pytest, no test framework code, no test dependencies; see the
# multi-stage build note above, RISK-002) keeps that note's intent intact
# while still giving the smoke test something to compare against inside
# the image that actually runs it. Already inside this image's build
# context (services/orchestrator) — no separate staging step needed,
# unlike contracts/ above.
COPY tests/golden/daily-signal-loop.expected.json ./tests/golden/daily-signal-loop.expected.json

USER app

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
