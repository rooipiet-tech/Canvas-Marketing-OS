# Canvas Marketing OS — Vault service container image.
#
# Built and pushed by .github/workflows/vault-image.yml via OIDC to the
# single shared platform ACR (infra/modules/container-registry.bicep).
# Runs as ca-vault (infra/modules/vault/container-app.bicep), internal
# ingress only, VNet-integrated into cae-cmos-dev.

FROM python:3.12-slim AS base

WORKDIR /app

# No OS package build toolchain required: asyncpg ships manylinux wheels
# for this base image's platform, so no gcc/libpq-dev layer is needed.
COPY requirements.txt requirements-test.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY vault ./vault
# tests/ + requirements-test.txt ride along in the same image so
# caj-vault-smoke-test (infra/modules/vault/smoke-test-job.bicep) can
# `pip install -r requirements-test.txt && pytest tests/` at job-run time
# without a second image build — requirements-test.txt is NOT installed
# into the default runtime layer above, so the production app image stays
# lean.
COPY tests ./tests

ENV PYTHONUNBUFFERED=1 \
    PORT=8000

EXPOSE 8000

# /health is dependency-free (no DB round-trip) — see vault/main.py —
# so it is safe to use both as the container HEALTHCHECK and as the
# Container Apps ingress probe target (infra/modules/vault/container-app.bicep).
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)" || exit 1

CMD ["uvicorn", "vault.main:app", "--host", "0.0.0.0", "--port", "8000"]
