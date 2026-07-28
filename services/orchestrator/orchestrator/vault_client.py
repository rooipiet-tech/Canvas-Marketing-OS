"""Best-effort, non-blocking write of coarse task status to Vault's
agent_runs (C1 hybrid persistence, AC-016/AC-016b/AC-017).

This is a thin, swappable adapter coded against the unmerged/unfrozen
services/vault snapshot (C3) — see AC-033, re-verified against origin/main
immediately before PR. write_status_best_effort NEVER raises out of this
function regardless of Vault's reachability: on any connection/timeout/
HTTP-status failure it logs a WARNING, increments the orchestrator's own
queryable vault_write_failed_count (+ a matching task_transitions audit
row, via db.increment_vault_write_failure), and returns — the orchestrator's
own schema transition (db.transition, called by the caller before this)
always succeeds independent of this call's outcome.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from orchestrator import config
from orchestrator.logging_config import get_logger, log_event

logger = get_logger("vault_client")


def write_status_best_effort(
    task_id: str,
    status: str,
    db: Any,
    database_url: str | None = None,
    vault_api_url: str | None = None,
) -> None:
    url = vault_api_url or config.VAULT_API_URL
    if not url:
        log_event(
            logger,
            logging.WARNING,
            "vault_write_skipped_no_url",
            task_id=task_id,
            status=status,
        )
        if db is not None:
            db.increment_vault_write_failure(task_id, database_url=database_url)
        return

    try:
        with httpx.Client(timeout=2.0) as http_client:
            response = http_client.post(
                f"{url.rstrip('/')}/agent_runs",
                json={"task_id": task_id, "status": status},
            )
            response.raise_for_status()
    except (
        httpx.ConnectError,
        httpx.TimeoutException,
        httpx.HTTPStatusError,
        httpx.RequestError,
    ) as exc:
        log_event(
            logger,
            logging.WARNING,
            "vault_write_failed",
            task_id=task_id,
            status=status,
            error=str(exc),
        )
        if db is not None:
            db.increment_vault_write_failure(task_id, database_url=database_url)
        return
