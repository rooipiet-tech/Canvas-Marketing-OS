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

RUN pip install --no-cache-dir --prefix=/install -e .

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
