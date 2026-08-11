"""GET /tasks/{task_id}/review — human-review detail for one task
(F-TEAMS-CARD-REVIEW-LINK, 11 Aug 2026).

Backs the console's GET /review/{task_id} page, which is what the
orchestrator's Teams "needs edit" and "retry exhausted" Adaptive Cards'
new "Review in console" button (orchestrator/teams_notify.py) links to.
The cards themselves only ever carried a 280-char draft_excerpt and the
violations list already in the card body -- this endpoint is for the full
draft text, resolved fresh from Vault on every call via the exact same
resolve_lineage_result() + vault.get_asset()/get_brief() path
dispatch.qa_review_handler() itself uses. Nothing is persisted here:
task_state.result_ref (db.set_result_ref) is documented as ids/hashes
only, never free text (AC-020/C8's no-client-data discipline) -- the full
draft was never available by re-reading result_ref, and still isn't; this
module reads it from Vault on demand instead, same as qa_review_handler
does when it first needs the draft for QA.

Mirrors run_state.py's build_run_state(task_ref, db) -> dict | None shape
so main.py's route stays a thin wrapper -- unit-testable by monkeypatching
db.get_task / resolve_lineage_result / build_vault_client directly,
without a live Postgres or Vault, the same way test_run_state.py tests
build_run_state.
"""

from __future__ import annotations

import base64
import logging
from typing import Any

from orchestrator.dispatch import build_vault_client, resolve_lineage_result
from orchestrator.logging_config import get_logger, log_event, sanitize_exception_text

logger = get_logger("task_review")


def _resolve_draft_text(task_id: str, db: Any) -> tuple[str | None, str | None]:
    """Returns (draft_text, draft_error) -- exactly one is non-None,
    unless there's simply no lineage to resolve (both None)."""
    try:
        lineage = resolve_lineage_result(task_id, db)
    except Exception as exc:  # noqa: BLE001 - a DB outage must degrade, not raise
        log_event(
            logger,
            logging.WARNING,
            "task_review_lineage_lookup_failed",
            task_id=task_id,
            error=sanitize_exception_text(exc),
        )
        return None, "draft content temporarily unavailable"

    if lineage is None:
        return None, "no ancestor task carries a result_ref to resolve draft content from"

    _, ancestor_ref = lineage
    try:
        with build_vault_client() as vault:
            if "vault_asset_id" in ancestor_ref:
                asset = vault.get_asset(ancestor_ref["vault_asset_id"])
                return base64.b64decode(asset["content_base64"]).decode("utf-8"), None
            if "brief_id" in ancestor_ref:
                brief = vault.get_brief(ancestor_ref["brief_id"])
                return brief["body"] or "", None
    except Exception as exc:  # noqa: BLE001 - a Vault outage must degrade, not raise
        log_event(
            logger,
            logging.WARNING,
            "task_review_draft_fetch_failed",
            task_id=task_id,
            error=sanitize_exception_text(exc),
        )
        return None, "draft content temporarily unavailable"

    # Ancestor result_ref carries neither key (e.g. a request-approval
    # stage's result_ref shape) -- not an error, just nothing to show.
    return None, "ancestor task's result has no resolvable draft content"


def build_task_review(task_id: str, db: Any) -> dict[str, Any] | None:
    """Returns the review payload for `task_id`, or None if task_id is
    not a known task (caller returns 404)."""
    task = db.get_task(task_id)
    if task is None:
        return None

    draft_text, draft_error = _resolve_draft_text(task_id, db)

    return {
        "task_id": task["task_id"],
        "task_type": task["task_type"],
        "state": task["state"],
        "retry_count": task["retry_count"],
        "result_ref": task["result_ref"],
        "draft_text": draft_text,
        "draft_error": draft_error,
    }
