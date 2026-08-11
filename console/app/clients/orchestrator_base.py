"""OrchestratorClient Protocol -- one method, task-review detail.

F-TEAMS-CARD-REVIEW-LINK (11 Aug 2026): backs the console's new
GET /review/{task_id} page, which is what the orchestrator's Teams "needs
edit" and "retry exhausted" Adaptive Cards now link to (see
services/orchestrator/orchestrator/teams_notify.py). Unlike vault-api and
Gatekeeper, the orchestrator is a live, already-deployed service in THIS
same repo -- its GET /tasks/{task_id}/review endpoint
(services/orchestrator/main.py) is the real, currently-shipping shape,
not a mock stand-in for an unmerged contract. There is therefore no
mock/real duality here the way vault_api_mock.py/gatekeeper_mock.py have
one: tests inject a fake OrchestratorClient directly via FastAPI's
app.dependency_overrides, same as _FakeAppInsightsClient in
test_console_routes.py.
"""

from __future__ import annotations

from typing import Any, Protocol


class OrchestratorClient(Protocol):
    async def get_task_review(self, task_id: str) -> dict[str, Any] | None: ...
