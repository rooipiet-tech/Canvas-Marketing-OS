# Canvas Marketing OS — analytics-ingest service container image.
#
# Built and pushed by .github/workflows/analytics-image.yml via OIDC to the
# single shared platform ACR (infra/modules/container-registry.bicep). Runs
# as caj-analytics-nightly-ingest / caj-analytics-buffer-smoke
# (infra/modules/analytics), no ingress — a batch/CLI image, not a web
# service (see analytics_ingest/cli.py).

FROM python:3.12-slim AS base

WORKDIR /app

# No OS package build toolchain required: asyncpg ships manylinux wheels
# for this base image's platform, so no gcc/libpq-dev layer is needed.
COPY requirements.txt requirements-test.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY analytics_ingest ./analytics_ingest
COPY migrations ./migrations
# tests/ + requirements-test.txt ride along in the same image so
# caj-analytics-buffer-smoke and any ad hoc pytest run at job-run time can
# `pip install -r requirements-test.txt && pytest tests/` without a second
# image build — requirements-test.txt is NOT installed into the default
# runtime layer above, so the production image stays lean.
COPY tests ./tests

# analytics/contracts/*.schema.json is staged into ./contracts by
# analytics-image.yml's build step (per L-0062) before this Dockerfile's
# context is built — CONTRACTS_DIR points there so config.contracts_dir()
# resolves correctly inside the image (see analytics_ingest/config.py).
ENV PYTHONUNBUFFERED=1 \
    CONTRACTS_DIR=/app/contracts

CMD ["python", "-m", "analytics_ingest.cli", "--help"]
