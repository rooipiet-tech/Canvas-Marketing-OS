"""Task Worker function app — health stub only.

This is a repo-scaffold placeholder. Per SCOPE-001, /functions owns no
business logic in this build — only a static health-check HTTP trigger.
The real task-envelope consumer (contracts/service-bus/task-envelope.schema.json)
is implemented in a later wave.
"""

import json

import azure.functions as func

app = func.FunctionApp()


@app.route(route="health", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def health(req: func.HttpRequest) -> func.HttpResponse:
    """Static liveness/readiness probe."""
    return func.HttpResponse(
        json.dumps({"status": "ok"}),
        status_code=200,
        mimetype="application/json",
    )
