"""Model Gateway service — health stub only.

This is a repo-scaffold placeholder. Per SCOPE-001, /services owns no
business logic in this build — only a static health-check route. The real
completion endpoint described in contracts/model-gateway/openapi.yaml is
implemented in a later wave.
"""

from fastapi import FastAPI

app = FastAPI(title="model-gateway")


@app.get("/health")
def health() -> dict:
    """Static liveness/readiness probe."""
    return {"status": "ok"}
