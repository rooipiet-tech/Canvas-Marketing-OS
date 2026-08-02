# Canvas Marketing OS — mcp-web
#
# No existing Dockerfile convention exists in this repo yet (researcher
# finding); this mirrors services/model-gateway's FastAPI + uvicorn
# runtime shape as closely as a container build allows.
#
# Build context MUST be the mcp/ directory (not this server's own
# directory), since it also needs mcp/common:
#   docker build -f mcp/mcp-web/Dockerfile mcp/
# (deploy-mcp.yml's `az acr build` invocations use this same context.)
FROM python:3.12-slim

WORKDIR /app

# mcp_common is installed explicitly, first, from its own copied-in path —
# mcp-web/requirements.txt intentionally does NOT list it (a stale
# `-e ../common` line here previously broke this build entirely: relative
# to /app, no such path exists, and pip aborted with "../common is not a
# valid editable requirement"). Do not re-add a relative editable line to
# requirements.txt; this explicit step is the only supported way
# mcp_common gets installed into the image.
#
# INCIDENT (2026-08-02, live — caj-mcp-smoke, first successful build after
# PR #50's az acr login fix): mcp_common/telemetry.py's
# open_tool_call_span() does an unguarded `from telemetry_lib import
# start_span` (not inside its own try/except) as the first line of the
# function body, called unconditionally on every POST /mcp request by
# protocol.py's build_router — but this image never installed the
# telemetry-lib sibling package (services/telemetry-lib), so the very
# first real tool call would 500 with ModuleNotFoundError. Same bug class
# as PR #47 (gatekeeper/publisher) and PR #49 (orchestrator). deploy-
# mcp.yml's "Stage telemetry-lib into the build context" step copies the
# sibling package into ./telemetry-lib relative to this Dockerfile's
# mcp/ build context (shared by mcp-web/mcp-buffer/mcp-canva/mcp-smoke).
COPY common /mcp-common
COPY telemetry-lib /telemetry-lib
COPY mcp-web/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -e /mcp-common /telemetry-lib \
    && pip install --no-cache-dir -r /app/requirements.txt

COPY mcp-web/app /app/app
COPY mcp-web/tools.yaml /app/tools.yaml
COPY mcp-web/fixtures /app/fixtures

ENV PYTHONUNBUFFERED=1
EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
