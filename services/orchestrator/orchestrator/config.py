"""Centralized, non-crashing config reads for the orchestrator service.

Every value here is read with a safe default/None and only dereferenced
(and only fails loudly) at the call site that actually needs it. This is
what lets main.py's lifespan (AC-018) and worker.py degrade gracefully to
the local Service Bus double / a no-op DB layer instead of crashing
FastAPI startup when DATABASE_URL / SERVICE_BUS_NAMESPACE / VAULT_API_URL
are unset — required because AC-018's literal verify command runs
`uvicorn main:app` with none of those env vars set.
"""

from __future__ import annotations

import os

DATABASE_URL: str | None = os.environ.get("DATABASE_URL")
VAULT_API_URL: str | None = os.environ.get("VAULT_API_URL")
SERVICE_BUS_NAMESPACE: str | None = os.environ.get("SERVICE_BUS_NAMESPACE")

# Worker loop poll interval, seconds. Small default keeps tests fast;
# overridden via env var in production if needed.
WORKER_POLL_INTERVAL_S: float = float(os.environ.get("WORKER_POLL_INTERVAL_S", "1.0"))
