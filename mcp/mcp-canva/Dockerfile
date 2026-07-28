# Canvas Marketing OS — mcp-canva
#
# Build context MUST be the mcp/ directory (not this server's own
# directory), since it also needs mcp/common:
#   docker build -f mcp/mcp-canva/Dockerfile mcp/
FROM python:3.12-slim

WORKDIR /app

COPY common /mcp-common
COPY mcp-canva/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -e /mcp-common \
    && pip install --no-cache-dir -r /app/requirements.txt

COPY mcp-canva/app /app/app
COPY mcp-canva/tools.yaml /app/tools.yaml
COPY mcp-canva/fixtures /app/fixtures

ENV PYTHONUNBUFFERED=1
EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
