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
from orchestrator.logging_config import get_logger, log_event, sanitize_exception_text

logger = get_logger("vault_client")


def _record_vault_write_failure(db: Any, task_id: str, database_url: str | None) -> None:
    """Best-effort wrapper around db.increment_vault_write_failure (OR-004):
    that call itself can raise (e.g. RuntimeError when task_id isn't found
    in task_state), which would otherwise propagate out of
    write_status_best_effort and break its "never raises" guarantee. Any
    failure here is logged and swallowed.
    """
    if db is None:
        return
    try:
        db.increment_vault_write_failure(task_id, database_url=database_url)
    except Exception as exc:  # noqa: BLE001 - must never propagate
        log_event(
            logger,
            logging.ERROR,
            "vault_write_failure_accounting_failed",
            task_id=task_id,
            error=sanitize_exception_text(exc),
        )


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
        _record_vault_write_failure(db, task_id, database_url)
        return

    try:
        with httpx.Client(timeout=2.0) as http_client:
            response = http_client.post(
                f"{url.rstrip('/')}/agent_runs",
                json={"task_id": task_id, "status": status},
            )
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        # Vault was reachable but rejected the request (e.g. 4xx/5xx) — a
        # distinct event name from the unreachable case below so a genuine
        # integration bug isn't masked as ordinary connectivity degradation
        # (OR-003).
        log_event(
            logger,
            logging.WARNING,
            "vault_write_rejected",
            task_id=task_id,
            status=status,
            error=sanitize_exception_text(exc),
        )
        _record_vault_write_failure(db, task_id, database_url)
        return
    except (httpx.ConnectError, httpx.TimeoutException, httpx.RequestError) as exc:
        log_event(
            logger,
            logging.WARNING,
            "vault_write_unreachable",
            task_id=task_id,
            status=status,
            error=sanitize_exception_text(exc),
        )
        _record_vault_write_failure(db, task_id, database_url)
        return
