# Canvas Marketing OS — model-gateway container image.
#
# Production interpreter pin lives here (python:3.12-slim), not in
# pyproject.toml's requires-python: this image is what actually runs in
# Azure Container Apps, so 3.12 is enforced at the layer that matters.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# The two frozen contract files the gateway reads at RUNTIME:
# openapi.yaml drives request-shape validation and redaction-rules.yaml
# drives the redaction firewall, so without them EVERY /v1/completions
# request fails.
#
# They live at the repository root, in contracts/model-gateway/ — outside
# this image's build context (`services/model-gateway`), which Docker's COPY
# cannot reach. deploy-gateway.yml's "Stage the frozen contracts into the
# build context" step copies them into ./contracts/model-gateway/ here,
# immediately before `docker build` runs. This COPY is written out
# explicitly rather than left to the `COPY . .` above so that a build
# without that staging step FAILS LOUDLY at build time instead of producing
# an image that only breaks on its first real request.
COPY contracts/model-gateway/openapi.yaml \
     contracts/model-gateway/redaction-rules.yaml \
     ./contracts/model-gateway/

# Points config.contracts_dir() at the staged copies above. Without it the
# app falls back to walking two directories up from its own source file
# looking for a repository checkout, which does not exist inside this image.
ENV CONTRACTS_DIR=/app/contracts

# Matches infra/modules/gateway.bicep's ingress.targetPort.
EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
