# Canvas Marketing OS — mcp-canva
#
# Build context MUST be the mcp/ directory (not this server's own
# directory), since it also needs mcp/common:
#   docker build -f mcp/mcp-canva/Dockerfile mcp/
FROM python:3.12-slim

WORKDIR /app

# mcp_common is installed explicitly, first, from its own copied-in path —
# mcp-canva/requirements.txt intentionally does NOT list it (no
# `-e ../common`-style relative editable line here: relative to /app, no
# such path exists).
#
# INCIDENT (2026-08-02, live — same class as PR #47/#49): mcp_common/
# telemetry.py's open_tool_call_span() unconditionally imports the
# telemetry-lib sibling package (services/telemetry-lib) on every POST
# /mcp request — see mcp-web/Dockerfile's identical incident note for the
# full writeup. This image never installed it either.
COPY common /mcp-common
COPY telemetry-lib /telemetry-lib
COPY mcp-canva/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -e /mcp-common /telemetry-lib \
    && pip install --no-cache-dir -r /app/requirements.txt

COPY mcp-canva/app /app/app
COPY mcp-canva/tools.yaml /app/tools.yaml
COPY mcp-canva/fixtures /app/fixtures

ENV PYTHONUNBUFFERED=1
EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
