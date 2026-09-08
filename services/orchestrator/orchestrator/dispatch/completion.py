from __future__ import annotations

import logging
from typing import Any

from orchestrator.clients.gateway_client import GatewayClientError, OrchestratorGatewayClient
from orchestrator.clients.vault_client_ext import VaultClientExt
from orchestrator.dispatch_errors import DispatchError
from orchestrator.logging_config import log_event, sanitize_exception_text

from .core import logger
from .scan_shared import _build_ingest_user_content


def _complete_and_meter(
    gateway: OrchestratorGatewayClient,
    vault: VaultClientExt,
    *,
    model: str,
    system_prompt: str,
    user_content: str,
    agent_run_id: str,
    content_class: str | None = None,
    max_tokens: int = 1536,
    task_ref: str | None = None,
) -> tuple[dict[str, Any], float]:
    """One completion + a best-effort read-back of its REAL metered cost
    (model-gateway's own metering.py already wrote 3 costs rows
    automatically, keyed by agent_run_id -- this just reads the usd row
    back for the span's cost attribute; a lookup failure never blocks the
    handler, it only means the span's cost stays 0.0).

    ``content_class`` is an additive, optional pass-through to
    gateway.complete() -- see gateway_client.py's own note and
    model-gateway's completion.py/redaction.py (F-INGEST-PUBLIC-SOURCE, 4
    Aug 2026, heartbeat round 15). None (the default) for every caller
    except ingest-signals' redaction-fallback path below -- every other
    call site is byte-identical to before this parameter existed.

    ``max_tokens`` (F-WEDNESDAY-DRAFT-TRUNCATION, 9 Aug 2026, heartbeat
    round 28): additive, default 1536 -- OrchestratorGatewayClient.complete's
    own existing default, so every call site that doesn't pass this
    explicitly is byte-identical to before this parameter existed. Added
    because the 6 Wednesday-drafting handlers' shared 1536-token ceiling
    was silently truncating their longer JSON assets mid-object: a real
    weekly-content-loop run (round 28) showed draft-case-study dead-letter
    3/3 tries and draft-executive-ghostwrite fail 1/2 tries, both on
    ``model response was not valid JSON: Expecting ',' delimiter`` --
    i.e. a truncated, not empty, completion (distinct from the
    F-EMPTY-COMPLETION-VISIBILITY case model-gateway's own completion.py
    already logs). See _draft_social_post_handler and
    draft_content_repurpose_handler for the actual per-asset-type values.

    ``task_ref`` (TD-07, docs/architecture/09-technical-debt.md): defaults
    to ``agent_run_id`` -- every call site's `agent_run_id` is by now a
    real Vault row created through VaultClientExt.create_agent_run_
    idempotent, so it is already stable across worker.py::_retry_or_
    dead_letter's outer retry (the SAME agent_run_id on attempt 1 and
    attempt 2). Passing it as `task_ref` is what makes model-gateway's own
    idempotency cache (TD-06, already deployed) actually engage for a
    retried handler's gateway call: attempt 2 gets back the SAME response
    attempt 1 already paid for, instead of billing the provider again.
    Explicit override exists for the one caller
    (_complete_ingest_with_redaction_fallback below) that legitimately
    issues MULTIPLE distinct completions under one shared agent_run_id
    within a single invocation -- there, reusing agent_run_id verbatim
    would wrongly serve a stale cached response from an earlier,
    differently-sourced attempt in the same fallback loop."""
    response = gateway.complete(
        model=model,
        system_prompt=system_prompt,
        user_content=user_content,
        agent_run_id=agent_run_id,
        content_class=content_class,
        max_tokens=max_tokens,
        task_ref=task_ref if task_ref is not None else agent_run_id,
    )
    cost = 0.0
    cost_id = response.get("cost_id")
    if cost_id:
        try:
            cost_row = vault.get_cost(cost_id)
            cost = float(cost_row.get("amount") or 0.0)
        except Exception as exc:  # noqa: BLE001 - span cost is best-effort observability only
            log_event(
                logger,
                logging.WARNING,
                "cost_lookup_failed",
                cost_id=cost_id,
                error=sanitize_exception_text(exc),
            )
    return response, cost

def _complete_ingest_with_redaction_fallback(
    gateway: OrchestratorGatewayClient,
    vault: VaultClientExt,
    *,
    sources: dict[str, Any],
    fetched: list[dict[str, str]],
    system_prompt: str,
    agent_run_id: str,
    captured: list[dict[str, str]] | None = None,
) -> tuple[dict[str, Any], float, list[dict[str, str]], list[dict[str, str]]]:
    """Complete the ingest-signals prompt, tolerating a redaction-firewall
    block on one or more of the fetched sources (F-INGEST-REDACTION, 4 Aug
    2026, heartbeat round 14).

    ingest-signals' user content is real fetched body text from live,
    uncontrolled news sources (the active scan profile's urls) -- unlike a static
    system prompt (see redaction.py's own INCIDENT note on that separate,
    already-fixed case), model-gateway's redaction firewall correctly
    scans this content on the `user` role, and real news text routinely
    contains a "full-name-like" (two consecutive Title-Case words) span
    whether or not it's actually PII. Previously a single blocked source
    failed the WHOLE ingest task (and, pre-PR-#62, cascaded into a ~15min
    stall for everything downstream).

    This never second-guesses or duplicates the firewall's decision --
    every attempt below is a REAL gateway call and the firewall's ruling
    is always authoritative. On a REDACTION_BLOCKED response, this drops
    ONE fetched source (in fetch order) and retries with what remains, so
    one problematic source degrades signal completeness instead of
    dead-lettering the whole task. It does NOT attempt to pinpoint
    exactly which source tripped the filter beyond removing them one at a
    time until a request clears -- favors simplicity and a small, bounded
    number of retries (at most len(fetched)) over precise attribution.
    Any other GatewayClientError (wrong error_code or none at all) is
    re-raised immediately, unchanged -- this fallback is scoped
    specifically to REDACTION_BLOCKED and must not mask a genuine gateway
    failure behind a source-dropping retry loop.

    F-INGEST-PUBLIC-SOURCE (4 Aug 2026, heartbeat round 15, Pieter's
    explicit ruling -- see redaction.py's INCIDENT 2 note): this was
    originally the one and only call site in the codebase that set
    content_class="public_source_content". That is no longer true --
    qa_review_handler, draft_research_brief_handler, and
    _single_draft_qa_review (round 34's per-draft replacement for the old
    _aggregate_qa_review -- see docs/content-learnings.md) each carry
    their own later, independently sign-off'd exemption, and
    _draft_social_post_handler picked one up
    in round 20 (F-WEEKLY-LOOP-DRAFT-PUBLIC-SOURCE, 7 Aug 2026 -- see
    that function's own docstring). It remains true that this exemption
    is correct here only because this function's own docstring above
    already establishes what `fetched` actually is -- real bodies from
    scan-profiles.yaml's public news domains, never Canvas
    client/customer data. No dispatch handler may set
    content_class="public_source_content" without its own equivalent,
    explicit Pieter sign-off recorded in its own docstring; doing so
    would silently widen a firewall exemption that is scoped narrowly,
    call site by call site, on purpose.
    """
    remaining = list(fetched)
    skipped: list[dict[str, str]] = []
    while remaining:
        user_content = _build_ingest_user_content(sources, remaining, captured)
        try:
            response, cost = _complete_and_meter(
                gateway,
                vault,
                model="claude-haiku",
                system_prompt=system_prompt,
                user_content=user_content,
                agent_run_id=agent_run_id,
                content_class="public_source_content",
                max_tokens=INGEST_MAX_TOKENS,
                # TD-07: this loop can issue several DISTINCT completions
                # under the SAME agent_run_id (one per source-dropping
                # retry) -- _complete_and_meter's agent_run_id-derived
                # default task_ref would wrongly serve the FIRST attempt's
                # cached response to every later, differently-sourced one.
                # `len(remaining)` is a stable per-iteration position (one
                # source is popped per iteration, never re-added), so each
                # iteration within one invocation gets its own cache slot.
                task_ref=f"{agent_run_id}:remaining={len(remaining)}",
            )
        except GatewayClientError as exc:
            if exc.error_code != "REDACTION_BLOCKED":
                raise
            dropped = remaining.pop(0)
            skipped.append(dropped)
            log_event(
                logger,
                logging.WARNING,
                "ingest_signals_source_redaction_blocked",
                url=dropped["url"],
                error=sanitize_exception_text(exc),
            )
            continue
        return response, cost, remaining, skipped
    raise DispatchError(
        "ingest-signals: every fetched source was blocked by the redaction firewall"
    )

INGEST_MAX_TOKENS = 2048


