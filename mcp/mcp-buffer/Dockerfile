# Canvas Marketing OS — mcp-buffer
#
# Build context MUST be the mcp/ directory (not this server's own
# directory), since it also needs mcp/common:
#   docker build -f mcp/mcp-buffer/Dockerfile mcp/
FROM python:3.12-slim

WORKDIR /app

# mcp_common is installed explicitly, first, from its own copied-in path —
# mcp-buffer/requirements.txt intentionally does NOT list it (no
# `-e ../common`-style relative editable line here: relative to /app, no
# such path exists).
COPY common /mcp-common
COPY mcp-buffer/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -e /mcp-common \
    && pip install --no-cache-dir -r /app/requirements.txt

COPY mcp-buffer/app /app/app
COPY mcp-buffer/tools.yaml /app/tools.yaml
COPY mcp-buffer/fixtures /app/fixtures

ENV PYTHONUNBUFFERED=1
EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
