# Canvas Marketing OS — orchestrator service image
#
# Pinned patch tag (never :latest for the base image, F7). Non-root
# runtime user: dependencies are installed as root (needed for
# system-level installs), then USER app is set BEFORE the final CMD, so
# the process itself never runs as root. COPY only — no ADD from a URL.

FROM python:3.12-slim-bookworm

WORKDIR /app

RUN useradd --create-home --shell /bin/bash app

COPY pyproject.toml ./
COPY main.py ./
COPY orchestrator ./orchestrator
COPY loops ./loops
COPY migrations ./migrations
COPY scripts ./scripts
COPY tests ./tests
COPY requirements-test.txt ./

RUN pip install --no-cache-dir -e . && \
    pip install --no-cache-dir -r requirements-test.txt

USER app

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
