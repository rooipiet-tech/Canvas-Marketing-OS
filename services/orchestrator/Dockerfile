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

USER app

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
