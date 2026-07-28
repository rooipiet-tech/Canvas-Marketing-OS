"""CONSOLE-001: get_task_queue shapes VaultApiMock.list_agent_runs() into
TaskRow objects."""

from __future__ import annotations

import pytest

from app.clients.vault_api_mock import VaultApiMock
from app.services import get_task_queue


@pytest.mark.asyncio
async def test_get_task_queue_returns_seeded_run() -> None:
    mock = VaultApiMock()
    mock.seed_agent_run(agent_name="brief-drafter", status="running")

    rows = await get_task_queue(mock)
    assert len(rows) == 1
    assert rows[0].agent_name == "brief-drafter"
    assert rows[0].status == "running"
