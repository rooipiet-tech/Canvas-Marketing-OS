"""OrchestratorHttpClient -- genuine httpx implementation of
OrchestratorClient, talking to ca-orchestrator's GET /tasks/{task_id}/review
over its internal ingress. See orchestrator_base.py's docstring for why
this has no mock counterpart."""

from __future__ import annotations

from typing import Any

import httpx


class OrchestratorHttpClient:
    def __init__(self, base_url: str, *, timeout: float = 10.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    async def get_task_review(self, task_id: str) -> dict[str, Any] | None:
        async with httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout) as client:
            response = await client.get(f"/tasks/{task_id}/review")
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()
