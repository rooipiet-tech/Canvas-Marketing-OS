# Canvas Marketing OS — console Dockerfile
#
# Build context is the REPO ROOT (not console/), because this image needs
# both console/ and the sibling services/telemetry-lib/ package copied in
# — Docker's COPY cannot reach outside its own build context (same
# constraint noted in services/model-gateway's sibling deploy-gateway.yml
# for contracts/). Build from repo root:
#
#   docker build -t console:smoke -f console/Dockerfile .
#
# This image is never pushed to the real shared ACR by any builder-run
# command — the only ACR push in this repo's console surface happens
# inside .github/workflows/deploy-console.yml's `build-and-push` job, in
# CI, gated by nothing but the cmos-dev environment's `deploy` job
# downstream of it (per the owner's no-live-execution ruling).

FROM python:3.12-slim

RUN useradd --create-home --uid 10001 appuser
WORKDIR /app

COPY services/telemetry-lib /app/services/telemetry-lib
COPY console /app/console

RUN pip install --no-cache-dir /app/services/telemetry-lib /app/console

USER appuser
WORKDIR /app/console

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
