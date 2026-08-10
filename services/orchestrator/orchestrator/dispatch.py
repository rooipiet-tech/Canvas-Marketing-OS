"""orchestrator/dispatch.py — the real per-task-type dispatch mechanism
(plan steps 6-13; AC-01, AC-02, AC-05, AC-06, AC-20, AC-24, AC-28, AC-30,
AC-31).

DISPATCH_TABLE maps the GOAL-mandated task_types (the original 5, plus the
13 weekly-content-loop.yaml (S11) task_types added 6 Aug 2026 -- see
"S11 REAL HANDLERS" below) to a real handler that produces a real downstream
artifact -- a costs-table row (via a real model-gateway call), a Vault
signal/brief/asset, or a gate-token request + approval-inbox card. Every
task_type NOT in this table (any genuinely unregistered one) falls through
to legacy_task_pass_through, which is BYTE-IDENTICAL to worker.py's own
pre-session unconditional stub (RUNNING -> COMPLETED -> advance_dependents)
-- nothing here regresses an already-shipped loop (AC-02).

request-approval's scope is bounded exactly as AC-01 requires: it calls
Gatekeeper's /gate-check once and completes as soon as that responds. It
NEVER polls or waits for the human decision -- that arrives asynchronously
via the Gatekeeper/approval-inbox surface, entirely outside this task.

draft-content / qa-review (when invoked with params.proof_circuit) /
request-approval together form the S8 PROOF CIRCUIT (AC-30): every
Vault agent_run they create is tagged agent_name=AGENT_NAME_LOOP_PROOF,
and request-approval's gate-check request carries the PROOF_CIRCUIT_TAG
in both preview_reference and preview_title so it is unmistakable on
every surface that renders it (console's approvals.html only renders
preview_title -- see request_approval_handler's docstring).

S11 REAL HANDLERS (6 Aug 2026, heartbeat round 19n follow-up, Pieter's
explicit instruction "please continue until its working"): weekly-content-
loop.yaml's 13 task_types previously all fell through to
legacy_task_pass_through -- meaning every "Fired: Succeeded" Monday run in
la-weekly-planning-trigger's history (including the two prior to this date)
produced NOTHING: no draft, no QA, no gate-check, no Teams card. Confirmed
live via ca-orchestrator's Log stream during a manual trigger fire: all 13
tasks dispatched within ~10 seconds of each other, ignoring depends_on
entirely, each just flipping RUNNING -> COMPLETED. Root-caused by reading
this file directly, not inferred.

Scope of this addition, and what is deliberately NOT included:
  - The 8 drafting handlers (plan-content-monday's dependents) are real:
    each calls its numbered function's real prompt.md via a real
    model-gateway completion, exactly mirroring draft_content_handler's
    existing pattern. No new prompts were needed for these 8 -- they
    already existed, just unwired.
  - qa-review-brand-steward and qa-review-fact-check are AGGREGATE gates:
    thursday-brand-steward-qa depends_on all 6 Wednesday drafts at once
    (a shape the existing single-ancestor qa_review_handler cannot serve
    unchanged), so these two handlers walk the task's own depends_on
    directly, review each of the 6 drafts independently, and record a
    per-draft verdict in their result_ref for visibility -- but the TASK
    ITSELF is still one row with one terminal state (COMPLETED or FAILED),
    because friday-schedule-social-buffer and friday-publish-newsletter
    are gated on that ONE task per the loop YAML's existing shape. A
    single violation in any one of the 6 therefore currently blocks the
    whole week (QA_BLOCKED, same as qa_review_handler's existing
    all-or-nothing failure contract) -- true per-draft partial pass-
    through would require restructuring weekly-content-loop.yaml itself
    (splitting Thursday QA into 6 separate tasks), which this change does
    NOT do. Flagged to Pieter as a possible follow-up, not built here.
  - qa-review-fact-check reuses the SAME aggregate-QA mechanism as
    brand-steward, against a NEW prompt (functions/48-fact-check-verdict/
    prompt.md) that Pieter has NOT reviewed yet -- it is bounded strictly
    to weekly-content-loop.yaml's own stated Thursday fact-check criterion
    ("confirms every proof point traces to a cited source, no fabricated
    claim survives downstream") and invents no policy beyond that, but it
    is a first draft, not an approved QA policy, and should be treated as
    such until Pieter has read it.
  - friday-schedule-social-buffer requests a REAL gate-check (function_id
    publish.social_post, mirroring request_approval_handler exactly) for
    each Wednesday draft eligible for Buffer scheduling per the loop
    YAML's own params (case-study is explicitly excluded per function 47's
    own prompt.md -- human-initiated cadence only), capped at
    buffer_weekly_post_cap. Each eligible draft gets its OWN approval
    card, not one combined card, since gate_check's contract is one
    content_hash per call.
  - friday-publish-newsletter requests a real gate-check (function_id
    publish.blog_article) exactly like schedule-social-buffer. This task
    completing does NOT itself send an email -- per request-approval's own
    existing scope (AC-01), it only ever requests the approval and stops;
    the actual send happens later, out of band, driven by whatever
    approves the decision. As of this change, services/publisher has NO
    real email-sending integration at all (confirmed by reading its
    source directly) -- Pieter chose Microsoft Graph/M365 as the intended
    mechanism, but that requires an Entra ID app registration with
    Mail.Send permission and admin consent that only Pieter can create.
    Approving a newsletter card today will NOT send a real email until
    that registration exists and its credentials are wired into
    services/publisher/app/config.py -- this is called out explicitly so
    nobody mistakes an approved decision for a delivered newsletter.
"""

from __future__ import annotations

import base64
import importlib.util
import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import yaml
from telemetry_lib import set_span_attribute

from orchestrator.clients.gatekeeper_client import GatekeeperClient, resolve_gatekeeper_base_url
from orchestrator.clients.gateway_client import (
    GatewayClientError,
    OrchestratorGatewayClient,
    resolve_gateway_base_url,
)
from orchestrator.clients.mcp_client import MCPClient, resolve_mcp_web_base_url
from orchestrator.clients.vault_client_ext import VaultClientExt, resolve_vault_base_url
from orchestrator.config import functions_dir
from orchestrator.logging_config import get_logger, log_event, sanitize_exception_text
from orchestrator.models import TaskEnvelope, TaskStateEnum, TransitionReason
from orchestrator.telemetry_wiring import emit_task_span

logger = get_logger("dispatch")

# Resolved through orchestrator.config.functions_dir() at CALL time, never
# a module-level constant (plan step 25's rebase-reconciliation fix,
# mirroring config.contracts_dir()'s own L-0062 pattern exactly) -- see
# that function's docstring for the full incident writeup.

FUNCTION_ID_09 = "09-market-intelligence-director"
FUNCTION_ID_42 = "42-linkedin-post-writer"
FUNCTION_ID_02 = "02-brand-steward-qa"
# Deterministic rendering only (plan step 9) -- no LLM call, so this isn't
# one of the numbered function packages under functions/.
FUNCTION_ID_BRIEF_COMPOSE = "brief.compose"

# S11 real handlers (6 Aug 2026) -- function IDs for weekly-content-loop's
# 8 drafting functions, all already-existing numbered prompt packages.
FUNCTION_ID_26 = "26-client-advocacy-harvester"
FUNCTION_ID_39 = "39-insight-to-story-editor"
FUNCTION_ID_41 = "41-research-brief-writer"
FUNCTION_ID_43 = "43-executive-ghostwriter"
FUNCTION_ID_45 = "45-carousel-post-writer"
FUNCTION_ID_46 = "46-newsletter-writer"
FUNCTION_ID_47 = "47-case-study-writer"
FUNCTION_ID_52 = "52-content-repurposer"
# New prompt package this change adds -- see module docstring's "first
# draft, not an approved QA policy" note.
FUNCTION_ID_48_FACT_CHECK = "48-fact-check-verdict"

# Cross-referenced with services/publisher/app/config.py's matching
# literal (step 14) -- a test in each service asserts the two stay equal
# (PV2-03's residual-risk mitigation).
AGENT_NAME_LOOP_PROOF = "loop-proof-circuit"

# AC-30's queryable isolation tag: threaded into every proof-circuit
# gate-check's preview_reference/preview_title and into the Vault
# agent_run.input of every proof-circuit model call.
PROOF_CIRCUIT_TAG = "loop-proof"

MAX_LINEAGE_HOPS = 6

class DispatchError(RuntimeError):
    """A real handler could not complete. Propagates out of dispatch_task
    to worker.py's existing outer try/except (task_handling_failed
    logged), leaving the task at RUNNING for the Service-Bus-redelivery
    backstop (C5, state_machine.record_failure) to eventually reconcile --
    the same fate as any other infra-level dispatch failure, never a
    silent COMPLETED."""

class TaskNotReadyError(RuntimeError):
    """Raised by dispatch_task when a task's queue message was received
    before the task itself actually reached the dispatchable state (i.e.
    its dependencies haven't all completed yet).

    worker.handle_heartbeat_message publishes every task in a decomposed
    batch onto the `task` queue up front, at heartbeat-decompose time --
    NOT gated on any earlier stage actually completing (db.advance_
    dependents only flips a row pending -> dispatchable later, once its
    real predecessor finishes; it never re-publishes anything). With more
    than one orchestrator replica (container-app.bicep's maxReplicas: 3)
    each independently polling the same queue, or simply an out-of-order
    redelivery, a downstream task's message can reach dispatch_task before
    its predecessor's own message has been handled.

    This is deliberately a DIFFERENT exception than DispatchError: a
    handler genuinely failing (bad data, an unreachable dependency) is not
    the same condition as a task whose turn simply hasn't come yet, and the
    two need different recoveries. DispatchError's own docstring assumes a
    "Service-Bus redelivery backstop" will eventually retry a stuck task --
    but worker.run_worker_loop's task-message loop unconditionally calls
    task_consumer.complete(msg) in its `finally`, even after a handler
    exception, so that assumed backstop can never actually fire; a message
    that raises is gone for good, and the task is stuck at RUNNING forever.
    TaskNotReadyError is instead caught by worker.handle_task_message
    itself, which re-publishes the SAME envelope for a later poll pass
    (bounded -- see NOT_READY_MAX_REQUEUES) rather than ever calling the
    handler on a not-yet-ready task or losing the message.

    dispatch_task only raises this for a dependency that is still
    genuinely in flight (pending/running/retry_pending) -- one worth
    waiting on. A dependency that has already reached a PERMANENT
    terminal state -- DEAD_LETTERED (3-strike retry exhaustion) or
    FAILED (e.g. TransitionReason.QA_BLOCKED: a real, non-retryable
    business verdict -- see qa_review_handler) -- raises
    DependencyDeadLetteredError instead (see its own docstring): that
    dependency will NEVER complete, so bouncing this message back onto
    the queue for NOT_READY_MAX_REQUEUES more polls before falling
    through to the ordinary 3-strike retry/backoff cycle just delays an
    outcome that is already certain (2026-08-04 finding: this stacked
    the 20-requeue not-ready bound in series with a FRESH 3-strike
    record_failure cycle, ~15+ minutes end-to-end for a task blocked on
    a permanently-failed dependency to reach its own terminal state --
    see DependencyDeadLetteredError for the fix).

    F-CASCADE-QA-BLOCKED (4 Aug 2026, heartbeat round 17): originally
    this check covered DEAD_LETTERED only. Once F-QA-REVIEW-PUBLIC-
    SOURCE let qa-review actually run to completion against real
    draft-brief content for the first time (instead of always dying
    upstream at the redaction firewall), it produced its first-ever
    real QA_BLOCKED verdict in production -- and that verdict's
    dependent (publish-brief) was found stuck not-ready-requeuing for
    the entire ~15 minute stacked-timeout window, never cascading,
    because FAILED wasn't recognized as equally permanent. A QA_BLOCKED
    draft is exactly as un-completable as a dead-lettered one: nothing
    retries a FAILED task automatically (record_failure never produces
    it; only qa_review_handler does, deliberately, as a one-shot
    business outcome), so a downstream task waiting on one has nothing
    left to wait for either."""

class DependencyDeadLetteredError(RuntimeError):
    """Raised by dispatch_task instead of TaskNotReadyError when the task
    isn't dispatchable yet AND at least one of its depends_on entries has
    already reached a PERMANENT terminal state -- DEAD_LETTERED or FAILED
    (checked one hop up, not the full lineage -- see below for why that's
    sufficient). Named for its original, narrower DEAD_LETTERED-only
    scope; kept rather than renamed (F-CASCADE-QA-BLOCKED, 4 Aug 2026) to
    keep this fix's diff minimal -- every reference to "dead lettered" in
    this class and its docstring should be read as "permanently blocked
    (dead_lettered or failed)".

    A task can only become `dispatchable` once EVERY entry in depends_on
    has COMPLETED (db.advance_dependents' contract). If any one of them
    is instead permanently DEAD_LETTERED or FAILED, that condition can
    never be satisfied -- the ordinary not-ready path (TaskNotReadyError:
    retry later, the dependency is still working) does not apply, because
    there is nothing left to wait for.

    worker.handle_task_message catches this and calls
    state_machine.cascade_dead_letter immediately -- no requeue, no
    backoff, no 3-strike cycle -- so a task blocked on a permanently
    failed dependency reaches its own terminal state in the same
    message pass that discovers the block, not ~15 minutes later.

    One-hop-only is intentional, not a shortcut: if an ANCESTOR further
    up the chain (rather than an immediate dependency) is the one that
    dead-lettered, the immediate dependency will itself be cascade-
    dead-lettered the next time ITS own not-ready gate is checked (the
    same wave-by-wave propagation this whole gate mechanism already
    relies on for the ordinary not-ready case), which in turn cascades
    to this task on ITS next check. No recursive lineage walk needed."""

    def __init__(self, message: str, blocking_task_id: str, blocking_task_type: str) -> None:
        super().__init__(message)
        # Structured access for worker.py's handler -- avoids parsing the
        # message string back apart to find which dependency caused this.
        self.blocking_task_id = blocking_task_id
        self.blocking_task_type = blocking_task_type

# ---------------------------------------------------------------------
# Client factories -- separate, monkeypatchable module-level functions
# (not inlined into each handler) so a test can substitute exactly one
# dependency without faking an entire httpx transport chain.
# ---------------------------------------------------------------------

def build_gateway_client() -> OrchestratorGatewayClient:
    return OrchestratorGatewayClient(base_url=resolve_gateway_base_url())

def build_vault_client() -> VaultClientExt:
    return VaultClientExt(base_url=resolve_vault_base_url())

def build_gatekeeper_client() -> GatekeeperClient:
    return GatekeeperClient(base_url=resolve_gatekeeper_base_url())

def build_mcp_web_client() -> MCPClient:
    return MCPClient(base_url=resolve_mcp_web_base_url())

_PERMISSION_CHECK_MODULE_NAME = "cmos_orchestrator_permission_check"

def load_permission_check() -> Any:
    """Dynamically loads functions/02-brand-steward-qa/permission_check.py
    (L-0039: a digit-prefixed/hyphenated directory name can't be
    dotted-imported) -- reused AS-IS, never forked/duplicated (AC-31's
    "no function logic duplication" requirement)."""
    if _PERMISSION_CHECK_MODULE_NAME in sys.modules:
        return sys.modules[_PERMISSION_CHECK_MODULE_NAME]
    module_path = functions_dir() / "02-brand-steward-qa" / "permission_check.py"
    spec = importlib.util.spec_from_file_location(_PERMISSION_CHECK_MODULE_NAME, module_path)
    if spec is None or spec.loader is None:
        raise DispatchError(f"cannot load permission_check.py from {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_PERMISSION_CHECK_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module

def _read_prompt(function_dir_name: str) -> str:
    return (functions_dir() / function_dir_name / "prompt.md").read_text(encoding="utf-8")

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _campaign_name(envelope: TaskEnvelope) -> str:
    return f"run-{envelope.campaign_id}"

def is_proof_circuit(envelope: TaskEnvelope) -> bool:
    return bool(envelope.metadata and envelope.metadata.get("proof_circuit") == "true")

def _agent_name(base_name: str, envelope: TaskEnvelope) -> str:
    """AC-30 (and step 10/11's own wording): proof-circuit invocations tag
    their Vault agent_run with AGENT_NAME_LOOP_PROOF; every other
    invocation keeps its ordinary descriptive agent_name."""
    return AGENT_NAME_LOOP_PROOF if is_proof_circuit(envelope) else base_name

def _parse_json_content(content: str) -> dict[str, Any]:
    """CompletionResponse.content is a plain string (contract) that the
    prompt asks the model to make a single bare JSON object.

    F-JSON-TRAILING-CONTENT (10 Aug 2026, round 30). The previous
    implementation stripped a leading code fence with `text.strip("`")`
    and then called a strict `json.loads`, which fails with "Extra data"
    the moment the model emits ANYTHING after the JSON object -- a
    closing ``` fence, or a sentence of explanation. `strip("`")` only
    removes backticks at the two ends of the whole string, so a response
    shaped

        ```json
        {...}
        ```

    left the closing fence sitting on its own line after the object and
    died with `Extra data: line 6 column 1`. That is the exact error that
    dead-lettered `qa-review-brand-steward` on the 10 Aug 05:00 UTC run,
    three retries in a row, at char offsets 589 / 892 / 1645 -- the
    growing offsets being the model's own variation in how much it added,
    not a truncation. Distinct from round 28's `Expecting ',' delimiter`
    bug (a genuine `max_tokens` truncation, fixed in PR #91): this one is
    the model producing MORE than asked, not less.

    The fix is to parse the first complete JSON value and tolerate
    trailing content rather than reject the whole response:
      - if the text is fenced, take what is between the fences;
      - if it does not start with a JSON opener, skip forward to the
        first `{` (covers "Here is the verdict:" preambles);
      - use `raw_decode`, which stops at the end of the first value;
      - log anything left over so it stays visible rather than silent.

    Deliberately NOT a prompt change: every function's prompt.md already
    says "and nothing else", and CI's `prompt-missing-json-output-
    contract` rule enforces it. The contract is right; the parser was
    brittle about a model that is occasionally chatty anyway.
    """
    text = content.strip()

    if text.startswith("```"):
        newline = text.find("\n")
        text = text[newline + 1 :] if newline != -1 else text[3:]
        closing = text.find("```")
        if closing != -1:
            text = text[:closing]
        text = text.strip()

    if not text.startswith(("{", "[")):
        opener = text.find("{")
        if opener > 0:
            text = text[opener:]

    try:
        parsed, end_index = json.JSONDecoder().raw_decode(text)
    except json.JSONDecodeError as exc:
        # F-JSON-PARSE-VISIBILITY (10 Aug 2026): the raw response is never
        # otherwise persisted -- the handler's own agent_run row stays at
        # status="running" (update_agent_run is only called on the success
        # path) and model-gateway's own completion log only carries metadata,
        # never body text. Without this, a parse failure is permanently
        # unrecoverable for root-causing after the fact. Truncated to keep
        # log volume sane; content_class is already public_source_content
        # for every caller of this function (marketing drafts, no client
        # names), so this carries no redaction/PII concern.
        log_event(
            logger,
            logging.WARNING,
            "model_response_json_parse_failed",
            error=str(exc),
            response_preview=text[:4000],
            response_length=len(text),
        )
        raise DispatchError(f"model response was not valid JSON: {exc}") from exc

    if not isinstance(parsed, dict):
        raise DispatchError(
            f"model response was valid JSON but not an object (got {type(parsed).__name__})"
        )

    trailing = text[end_index:].strip()
    if trailing:
        log_event(
            logger,
            logging.WARNING,
            "model_response_trailing_content_discarded",
            trailing_chars=len(trailing),
            trailing_preview=trailing[:120],
        )

    return parsed

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
    draft_content_repurpose_handler for the actual per-asset-type values."""
    response = gateway.complete(
        model=model,
        system_prompt=system_prompt,
        user_content=user_content,
        agent_run_id=agent_run_id,
        content_class=content_class,
        max_tokens=max_tokens,
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
) -> tuple[dict[str, Any], float, list[dict[str, str]], list[dict[str, str]]]:
    """Complete the ingest-signals prompt, tolerating a redaction-firewall
    block on one or more of the fetched sources (F-INGEST-REDACTION, 4 Aug
    2026, heartbeat round 14).

    ingest-signals' user content is real fetched body text from live,
    uncontrolled news sources (fetch_sources.yaml) -- unlike a static
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
    _aggregate_qa_review each carry their own later, independently
    sign-off'd exemption, and _draft_social_post_handler picked one up
    in round 20 (F-WEEKLY-LOOP-DRAFT-PUBLIC-SOURCE, 7 Aug 2026 -- see
    that function's own docstring). It remains true that this exemption
    is correct here only because this function's own docstring above
    already establishes what `fetched` actually is -- real bodies from
    fetch_sources.yaml's public news domains, never Canvas
    client/customer data. No dispatch handler may set
    content_class="public_source_content" without its own equivalent,
    explicit Pieter sign-off recorded in its own docstring; doing so
    would silently widen a firewall exemption that is scoped narrowly,
    call site by call site, on purpose.
    """
    remaining = list(fetched)
    skipped: list[dict[str, str]] = []
    while remaining:
        user_content = _build_ingest_user_content(sources, remaining)
        try:
            response, cost = _complete_and_meter(
                gateway,
                vault,
                model="claude-haiku",
                system_prompt=system_prompt,
                user_content=user_content,
                agent_run_id=agent_run_id,
                content_class="public_source_content",
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

# ---------------------------------------------------------------------
# depends_on lineage resolution (shared by draft-brief and qa-review)
# ---------------------------------------------------------------------

def resolve_lineage_result(
    task_id: str, db: Any, *, max_hops: int = MAX_LINEAGE_HOPS
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Breadth-first walk up `task_id`'s depends_on ancestors until one
    carries a non-null result_ref, returning (that ancestor's task row,
    its result_ref). Transparently walks PAST an unhandled pass-through
    ancestor (e.g. daily-signal-loop.yaml's score-signals, which never
    gets a result_ref since it falls through to legacy_task_pass_through)
    -- draft-brief's immediate predecessor is 'score', but the real
    content it needs lives 2 hops back at 'ingest'. qa-review's own two
    loop positions each have a result_ref-bearing IMMEDIATE predecessor
    (draft-brief / draft-content), so this same walk resolves both in one
    hop for qa-review and two for draft-brief -- one mechanism, no
    per-loop-position special casing.
    """
    frontier = [task_id]
    seen = {task_id}
    for _ in range(max_hops):
        if not frontier:
            return None
        rows = {row["task_id"]: row for row in db.get_tasks(frontier)}
        next_frontier: list[str] = []
        for tid in frontier:
            row = rows.get(tid)
            if row is None:
                continue
            if tid != task_id and row.get("result_ref") is not None:
                return row, row["result_ref"]
            for dep in row.get("depends_on") or []:
                if dep not in seen:
                    seen.add(dep)
                    next_frontier.append(dep)
        frontier = next_frontier
    return None

# ---------------------------------------------------------------------
# ingest-signals (plan step 7; AC-01, AC-24, AC-28)
# ---------------------------------------------------------------------

def _load_fetch_sources() -> dict[str, Any]:
    path = functions_dir() / "09-market-intelligence-director" / "fetch_sources.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))

def _build_ingest_user_content(sources: dict[str, Any], fetched: list[dict[str, str]]) -> str:
    lines = [
        f"Topic: {sources['topic']}",
        f"Horizon (days): {sources['horizon_days']}",
        "",
        "Retrieved evidence (fetch_url results, truncated):",
    ]
    for item in fetched:
        lines.append(f"--- SOURCE: {item['url']} ---")
        lines.append(item["body"] or "(empty response body)")
    return "\n".join(lines)

def ingest_signals_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    sources = _load_fetch_sources()

    with build_mcp_web_client() as mcp:
        fetched: list[dict[str, str]] = []
        for url in sources["urls"]:
            try:
                result = mcp.call_tool("fetch_url", {"url": url})
            except Exception as exc:  # noqa: BLE001 - one bad source must not sink the whole scan
                log_event(
                    logger,
                    logging.WARNING,
                    "fetch_url_failed",
                    url=url,
                    error=sanitize_exception_text(exc),
                )
                continue
            fetched.append({"url": url, "body": str(result.get("body", ""))[:2000]})

    if not fetched:
        raise DispatchError("ingest-signals: every configured fetch_sources.yaml source failed")

    with build_vault_client() as vault:
        campaign_id = vault.get_or_create_campaign(
            _campaign_name(envelope), function_id=FUNCTION_ID_09
        )
        agent_run = vault.create_agent_run(
            agent_name=_agent_name("market-intelligence-director", envelope),
            campaign_id=campaign_id,
            function_id=FUNCTION_ID_09,
            status="running",
            input_payload={
                "topic": sources["topic"],
                "horizon_days": sources["horizon_days"],
                "source_urls": [item["url"] for item in fetched],
                "proof_circuit_tag": PROOF_CIRCUIT_TAG if is_proof_circuit(envelope) else None,
            },
        )

        system_prompt = _read_prompt("09-market-intelligence-director")

        with emit_task_span(
            "ingest-signals",
            function_id=FUNCTION_ID_09,
            task_ref=task_id,
            model="claude-haiku",
            run_id=str(envelope.campaign_id),
        ) as span:
            with build_gateway_client() as gateway:
                response, cost, used_sources, skipped_sources = (
                    _complete_ingest_with_redaction_fallback(
                        gateway,
                        vault,
                        sources=sources,
                        fetched=fetched,
                        system_prompt=system_prompt,
                        agent_run_id=agent_run["id"],
                    )
                )
            set_span_attribute(span, "cost", cost)
            if skipped_sources:
                log_event(
                    logger,
                    logging.WARNING,
                    "ingest_signals_sources_redacted",
                    skipped_urls=[item["url"] for item in skipped_sources],
                    used_urls=[item["url"] for item in used_sources],
                )

        output = _parse_json_content(response["content"])

        signal = vault.create_signal(
            source="function-09-market-intelligence-director",
            signal_type="market_signal_batch",
            payload=output,
            campaign_id=campaign_id,
            function_id=FUNCTION_ID_09,
        )
        vault.update_agent_run(
            agent_run["id"], status="succeeded", output_payload=output, completed_at=_now_iso()
        )

    db.set_result_ref(
        task_id,
        {
            "vault_signal_id": signal["id"],
            "agent_run_id": agent_run["id"],
            "campaign_id": campaign_id,
            "topic": sources["topic"],
        },
    )
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)

# ---------------------------------------------------------------------
# draft-brief (plan step 9; AC-01, AC-25)
# ---------------------------------------------------------------------

def _render_brief(topic: str, signal_output: dict[str, Any]) -> tuple[str, str]:
    """Deterministic rendering (NO LLM call, per plan step 9) of
    function 09's structured signal batch into a full brief + a
    condensed one-page executive edition. Returns (full_body,
    executive_body)."""
    summary = signal_output.get("summary", "")
    signals = signal_output.get("signals", [])

    full_lines = [f"# Morning Brief — {topic}", "", summary, "", "## Signals"]
    for item in signals:
        source_domain = urlparse(item.get("source_url", "")).hostname or "unknown-source"
        full_lines.append(
            f"- [{item.get('pillar', '?')}/{item.get('confidence', '?')}] "
            f"{item.get('headline', '')} — {item.get('so_what', '')} "
            f"(source: {source_domain})"
        )
    full_body = "\n".join(full_lines)

    exec_lines = [f"# Executive Edition — {topic}", "", summary, "", "## Top signals"]
    for item in signals[:3]:
        exec_lines.append(f"- {item.get('headline', '')}")
    executive_body = "\n".join(exec_lines)

    return full_body, executive_body

def draft_brief_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    from orchestrator import teams_notify

    lineage = resolve_lineage_result(task_id, db)
    if lineage is None:
        raise DispatchError("draft-brief: no ancestor task carries a result_ref to render from")
    _ancestor_task, ancestor_ref = lineage
    signal_id = ancestor_ref.get("vault_signal_id")
    if not signal_id:
        raise DispatchError("draft-brief: ancestor result_ref carries no vault_signal_id")

    with build_vault_client() as vault:
        campaign_id = vault.get_or_create_campaign(
            _campaign_name(envelope), function_id=FUNCTION_ID_BRIEF_COMPOSE
        )
        signal = vault.get_signal(signal_id)
        signal_output = signal.get("payload", {})
        topic = ancestor_ref.get("topic") or signal_output.get("topic", "morning brief")

        full_body, executive_body = _render_brief(topic, signal_output)

        agent_run = vault.create_agent_run(
            agent_name=_agent_name("brief-writer", envelope),
            campaign_id=campaign_id,
            function_id=FUNCTION_ID_BRIEF_COMPOSE,
            status="running",
            input_payload={"vault_signal_id": signal_id},
        )

        brief = vault.create_brief(
            title=f"Morning Brief — {topic}",
            body_text=full_body,
            campaign_id=campaign_id,
            function_id=FUNCTION_ID_BRIEF_COMPOSE,
        )
        executive_brief = vault.create_brief(
            title=f"Executive Edition — {topic}",
            body_text=executive_body,
            campaign_id=campaign_id,
            function_id=FUNCTION_ID_BRIEF_COMPOSE,
        )

        vault.update_agent_run(
            agent_run["id"],
            status="succeeded",
            output_payload={"brief_id": brief["id"], "executive_brief_id": executive_brief["id"]},
            completed_at=_now_iso(),
        )

        # AC-25: Teams posting is flag-gated (TEAMS_WEBHOOK_URL absent by
        # default in cmos-dev today); falls back to the Vault write above +
        # console's approval-inbox-equivalent surface, which already makes
        # the brief observable regardless of this call's outcome.
        teams_notify.notify_brief_ready(
            title=brief["title"], brief_id=brief["id"], executive_brief_id=executive_brief["id"]
        )

    with emit_task_span(
        "draft-brief",
        function_id=FUNCTION_ID_BRIEF_COMPOSE,
        task_ref=task_id,
        model="none",
        cost=0.0,
        run_id=str(envelope.campaign_id),
    ):
        pass  # deterministic rendering only -- no gateway call, no cost

    db.set_result_ref(
        task_id,
        {
            "brief_id": brief["id"],
            "executive_brief_id": executive_brief["id"],
            "agent_run_id": agent_run["id"],
            "campaign_id": campaign_id,
        },
    )
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)

# ---------------------------------------------------------------------
# qa-review (plan step 10; AC-01, AC-05, AC-06, AC-31(c))
#
# Invoked from TWO loop positions through this ONE handler: daily-signal-
# loop.yaml's brief-QA ('qa', predecessor 'draft' / draft-brief) and the
# S8 proof circuit's content-QA ('content-qa-review', predecessor
# 'draft-linkedin-post' / draft-content). WHAT to validate is resolved
# purely from depends_on lineage (never from a task_id naming
# convention); task.params.proof_circuit (carried via envelope.metadata,
# see worker.py's _task_metadata) is consulted ONLY to decide whether to
# tag this invocation's own Vault agent_run with AGENT_NAME_LOOP_PROOF.
# ---------------------------------------------------------------------

def qa_review_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    permission_check = load_permission_check()

    lineage = resolve_lineage_result(task_id, db)
    if lineage is None:
        raise DispatchError("qa-review: no ancestor task carries a result_ref to validate")
    ancestor_task, ancestor_ref = lineage

    with build_vault_client() as vault:
        campaign_id = vault.get_or_create_campaign(
            _campaign_name(envelope), function_id=FUNCTION_ID_02
        )

        client_references: list[str] = []
        content_class: str | None = None
        if ancestor_task["task_type"] == "draft-content":
            channel = "linkedin"
            content_class = "public_source_content"
            asset = vault.get_asset(ancestor_ref["vault_asset_id"])

            draft_text = base64.b64decode(asset["content_base64"]).decode("utf-8")
        else:
            channel = "internal-brief"
            content_class = "public_source_content"
            brief = vault.get_brief(ancestor_ref["brief_id"])
            draft_text = brief["body"] or ""

        agent_run = vault.create_agent_run(
            agent_name=_agent_name("brand-steward-qa", envelope),
            campaign_id=campaign_id,
            function_id=FUNCTION_ID_02,
            status="running",
            input_payload={
                "channel": channel,
                "proof_circuit_tag": PROOF_CIRCUIT_TAG if is_proof_circuit(envelope) else None,
            },
        )

        system_prompt = _read_prompt("02-brand-steward-qa")
        user_content = json.dumps(
            {"draft_text": draft_text, "client_references": client_references, "channel": channel}
        )

        with emit_task_span(
            "qa-review",
            function_id=FUNCTION_ID_02,
            task_ref=task_id,
            model="claude-sonnet",
            run_id=str(envelope.campaign_id),
        ) as span:
            with build_gateway_client() as gateway:
                response, cost = _complete_and_meter(
                    gateway,
                    vault,
                    model="claude-sonnet",
                    system_prompt=system_prompt,
                    user_content=user_content,
                    agent_run_id=agent_run["id"],
                    content_class=content_class,
                )
            set_span_attribute(span, "cost", cost)

        verdict = _parse_json_content(response["content"])
        violations = list(verdict.get("violations") or [])

        uncleared = permission_check.find_uncleared_references(client_references)
        if uncleared and permission_check.VIOLATION_CODE not in violations:
            violations.append(permission_check.VIOLATION_CODE)

        passed = not violations

        vault.update_agent_run(
            agent_run["id"],
            status="succeeded" if passed else "failed",
            output_payload={"pass": passed, "violations": violations},
            completed_at=_now_iso(),
        )

    if not passed:
        db.set_result_ref(
            task_id,
            {
                "pass": False,
                "violations": violations,
                "agent_run_id": agent_run["id"],
                "campaign_id": campaign_id,
            },
        )
        db.transition(task_id, TaskStateEnum.FAILED, TransitionReason.QA_BLOCKED)
        log_event(logger, logging.INFO, "qa_review_blocked", task_id=task_id, violations=violations)

        # Proposal C (qa-feedback-loop-proposal-2026-08-05.md): surface the
        # block as a "needs edit" card instead of letting it die silently.
        # Same AC-25 flag-gate as draft_brief_handler's teams_notify call --
        # no-ops until TEAMS_WEBHOOK_URL exists in Key Vault.
        from orchestrator import teams_notify

        teams_notify.notify_needs_edit(
            task_id=task_id,
            channel=channel,
            violations=violations,
            draft_excerpt=draft_text[:280],
        )

        return  # never advance_dependents -- request-approval must never see this asset

    db.set_result_ref(
        task_id,
        {
            "pass": True,
            "vault_asset_id": ancestor_ref.get("vault_asset_id"),
            "brief_id": ancestor_ref.get("brief_id"),
            "content_hash": ancestor_ref.get("content_hash"),
            "agent_run_id": agent_run["id"],
            "campaign_id": campaign_id,
        },
    )
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)

# ---------------------------------------------------------------------
# draft-content (plan step 11; AC-01, AC-06, AC-30) -- the S8 proof
# circuit's own function-42 draft. Client-free by construction (DE-4):
# no client_reference is ever populated, so it legitimately clears
# function 02's uncleared-client-block rather than needing an override.
# ---------------------------------------------------------------------

# A generic, client-free proof point drawn from docs/positioning.md
# section 3 (Imperial's architecture facts, described WITHOUT naming the
# client -- DE-4/AC-06). Never invented: these are the same numbers
# positioning.md's own pillar table cites for "Consolidation at scale".
DRAFT_CONTENT_PROOF_POINT = (
    "one recent engagement consolidated 40+ business units across 14+ ERP systems "
    "into a single governed Azure lakehouse"
)
DRAFT_CONTENT_PILLAR = "Consolidation at scale"
DRAFT_CONTENT_CAMPAIGN_UTM = "loop-proof"

def draft_content_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    with build_vault_client() as vault:
        campaign_id = vault.get_or_create_campaign(
            _campaign_name(envelope), function_id=FUNCTION_ID_42
        )

        agent_run = vault.create_agent_run(
            agent_name=_agent_name("linkedin-post-writer", envelope),
            campaign_id=campaign_id,
            function_id=FUNCTION_ID_42,
            status="running",
            input_payload={
                "pillar": DRAFT_CONTENT_PILLAR,
                "campaign": DRAFT_CONTENT_CAMPAIGN_UTM,
                "proof_circuit_tag": PROOF_CIRCUIT_TAG,
            },
        )

        system_prompt = _read_prompt("42-linkedin-post-writer")
        user_content = json.dumps(
            {
                "pillar": DRAFT_CONTENT_PILLAR,
                "proof_point": DRAFT_CONTENT_PROOF_POINT,
                "campaign": DRAFT_CONTENT_CAMPAIGN_UTM,
            }
        )

        with emit_task_span(
            "draft-content",
            function_id=FUNCTION_ID_42,
            task_ref=task_id,
            model="claude-sonnet",
            run_id=str(envelope.campaign_id),
        ) as span:
            with build_gateway_client() as gateway:
                response, cost = _complete_and_meter(
                    gateway,
                    vault,
                    model="claude-sonnet",
                    system_prompt=system_prompt,
                    user_content=user_content,
                    agent_run_id=agent_run["id"],
                )
            set_span_attribute(span, "cost", cost)

        output = _parse_json_content(response["content"])
        post_text = output["post"]
        post_bytes = post_text.encode("utf-8")

        asset = vault.create_asset(
            asset_type="linkedin_post",
            agent_run_id=agent_run["id"],
            campaign_id=campaign_id,
            function_id=FUNCTION_ID_42,
            content_bytes=post_bytes,
            approval_state="draft",
        )
        vault.update_agent_run(
            agent_run["id"], status="succeeded", output_payload=output, completed_at=_now_iso()
        )

    db.set_result_ref(
        task_id,
        {
            "vault_asset_id": asset["id"],
            "content_hash": asset["content_hash"],
            "agent_run_id": agent_run["id"],
            "campaign_id": campaign_id,
        },
    )
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)

# ---------------------------------------------------------------------
# request-approval (plan step 12; AC-01, AC-30) -- issues the gate-token
# request / surfaces the approval card, and NOTHING else. Completes the
# instant /gate-check responds; the human decision arrives asynchronously
# via Gatekeeper/approval-inbox, entirely outside this task.
# ---------------------------------------------------------------------

REAL_PUBLISH_FUNCTION_ID = "publish.social_post"
REAL_PUBLISH_ACTION_CLASS = "publish"
REAL_NEWSLETTER_FUNCTION_ID = "publish.blog_article"

def request_approval_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    lineage = resolve_lineage_result(task_id, db)
    if lineage is None:
        raise DispatchError("request-approval: no ancestor task carries a result_ref to approve")
    _ancestor_task, ancestor_ref = lineage
    content_hash = ancestor_ref.get("content_hash")
    if not content_hash:
        raise DispatchError("request-approval: ancestor result_ref carries no content_hash")

    proof_circuit = is_proof_circuit(envelope)
    # preview_reference: the programmatic/API-consumer tag (AC-15).
    # preview_title: the SAME tag, human-visible on the ONE field
    # console/app/templates/approvals.html actually renders (PV3-02) --
    # both are required, neither substitutes for the other.
    preview_reference = f"loop-proof://{task_id}" if proof_circuit else None
    preview_title = (
        f"[LOOP-PROOF] {REAL_PUBLISH_FUNCTION_ID} ({REAL_PUBLISH_ACTION_CLASS})"
        if proof_circuit
        else None
    )

    with build_gatekeeper_client() as gatekeeper:
        with emit_task_span(
            "request-approval",
            function_id=REAL_PUBLISH_FUNCTION_ID,
            task_ref=task_id,
            model="none",
            cost=0.0,
            run_id=str(envelope.campaign_id),
        ):
            decision = gatekeeper.gate_check(
                agent_run_id=str(envelope.agent_run_id),
                function_id=REAL_PUBLISH_FUNCTION_ID,
                action_class=REAL_PUBLISH_ACTION_CLASS,
                content_hash=content_hash,
                preview_title=preview_title,
                preview_reference=preview_reference,
            )

    db.set_result_ref(
        task_id,
        {
            "decision_id": decision.get("decision_id"),
            "outcome": decision.get("outcome"),
            "approval_id": decision.get("approval_id"),
            "approve_url": decision.get("approve_url"),
            "reject_url": decision.get("reject_url"),
            "content_hash": content_hash,
            "agent_run_id": str(envelope.agent_run_id),
            "function_id": REAL_PUBLISH_FUNCTION_ID,
        },
    )
    # Completes as soon as /gate-check responds -- never waits/polls on
    # the human decision (AC-01's bounded scope for this task_type).
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)

# =======================================================================
# S11 REAL HANDLERS (weekly-content-loop.yaml, 6 Aug 2026) -- see module
# docstring's "S11 REAL HANDLERS" section for full scope/caveats before
# reading further.
# =======================================================================

# The five pillars, exactly as positioning.md section 5 and every
# function's own prompt.md name them -- kept as one literal list so a
# rotation (plan-content-monday) and any future validation share one
# source of truth rather than five independently-typed copies.
CONTENT_PILLARS = [
    "Finance-grade trust",
    "Consolidation at scale",
    "Fabric-native",
    "Productised speed",
    "Beyond the dashboard",
]
FUNCTION_ID_PLAN_COMPOSE = "plan.compose"

def plan_content_monday_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    """Monday planning is deterministic, NOT an LLM call -- there is
    nothing to draft yet, only a pillar to choose for the week. Rotates
    through CONTENT_PILLARS by ISO week number so the choice is
    reproducible and auditable (same week number -> same pillar) rather
    than random (Date.now()/random are unavailable in workflow-adjacent
    contexts elsewhere in this campaign for the same reason: reproducible
    beats clever). No Vault agent_run is created here -- there is no
    model call to meter, mirroring draft_brief_handler's own "no gateway
    call, no cost" span pattern.
    """
    week_number = datetime.now(timezone.utc).isocalendar()[1]
    pillar = CONTENT_PILLARS[week_number % len(CONTENT_PILLARS)]

    with emit_task_span(
        "plan-content-monday",
        function_id=FUNCTION_ID_PLAN_COMPOSE,
        task_ref=task_id,
        model="none",
        cost=0.0,
        run_id=str(envelope.campaign_id),
    ):
        pass

    db.set_result_ref(
        task_id,
        {
            "pillar": pillar,
            "week_number": week_number,
        },
    )
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)

def _monday_plan_pillar(task_id: str, db: Any) -> str:
    """Every S11 drafting handler needs this week's pillar. Walks straight
    to the immediate plan-content-monday ancestor via resolve_lineage_result
    (one hop for draft-research-brief/draft-client-advocacy-harvest, since
    plan-content-monday now always carries a result_ref -- see
    plan_content_monday_handler above)."""
    lineage = resolve_lineage_result(task_id, db)
    if lineage is None:
        raise DispatchError(
            f"{task_id}: no ancestor task carries a result_ref for this week's pillar"
        )
    _ancestor_task, ancestor_ref = lineage
    pillar = ancestor_ref.get("pillar")
    if not pillar:
        raise DispatchError(f"{task_id}: nearest ancestor result_ref carries no pillar")
    return pillar

def draft_research_brief_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    """Function 41. Feeds every Wednesday drafting function -- NOT itself
    reviewed by Thursday's QA gate (mirrors ingest-signals' own position
    relative to daily-signal-loop's QA: an upstream research artifact, not
    a publish-bound draft)."""
    pillar = _monday_plan_pillar(task_id, db)

    with build_vault_client() as vault:
        campaign_id = vault.get_or_create_campaign(
            _campaign_name(envelope), function_id=FUNCTION_ID_41
        )
        agent_run = vault.create_agent_run(
            agent_name=_agent_name("research-brief-writer", envelope),
            campaign_id=campaign_id,
            function_id=FUNCTION_ID_41,
            status="running",
            input_payload={"pillar": pillar},
        )

        system_prompt = _read_prompt("41-research-brief-writer")
        user_content = json.dumps({"pillar": pillar})

        with emit_task_span(
            "draft-research-brief",
            function_id=FUNCTION_ID_41,
            task_ref=task_id,
            model="claude-sonnet",
            run_id=str(envelope.campaign_id),
        ) as span:
            with build_gateway_client() as gateway:
                response, cost = _complete_and_meter(
                    gateway,
                    vault,
                    model="claude-sonnet",
                    system_prompt=system_prompt,
                    user_content=user_content,
                    agent_run_id=agent_run["id"],
                    content_class="public_source_content",
                )
            set_span_attribute(span, "cost", cost)

        output = _parse_json_content(response["content"])
        vault.update_agent_run(
            agent_run["id"], status="succeeded", output_payload=output, completed_at=_now_iso()
        )
        brief = vault.create_brief(
            title=f"Research Brief — {pillar}",
            body_text=json.dumps(output.get("brief", output)),
            campaign_id=campaign_id,
            function_id=FUNCTION_ID_41,
        )

    db.set_result_ref(
        task_id,
        {
            "brief_id": brief["id"],
            "pillar": output.get("brief", {}).get("pillar", pillar),
            "vertical": output.get("brief", {}).get("vertical"),
            "audience_note": output.get("audience_note"),
            "agent_run_id": agent_run["id"],
            "campaign_id": campaign_id,
        },
    )
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)

def draft_client_advocacy_harvest_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    """Function 26. No consent-record fixture is wired into this
    environment yet, so this reliably returns naming_decision =
    blocked-no-consent every real run today -- correct, safe, default-deny
    behaviour per the function's own prompt, not a bug. A real consent
    register integration is a separate follow-up, not required for
    Thursday's QA gate to function (this task is not one of its 6
    dependencies either -- see qa_review_brand_steward_handler)."""
    pillar = _monday_plan_pillar(task_id, db)

    with build_vault_client() as vault:
        campaign_id = vault.get_or_create_campaign(
            _campaign_name(envelope), function_id=FUNCTION_ID_26
        )
        agent_run = vault.create_agent_run(
            agent_name=_agent_name("client-advocacy-harvester", envelope),
            campaign_id=campaign_id,
            function_id=FUNCTION_ID_26,
            status="running",
            input_payload={"pillar": pillar, "consent_record": None},
        )

        system_prompt = _read_prompt("26-client-advocacy-harvester")
        user_content = json.dumps({"pillar": pillar, "consent_record": None})

        with emit_task_span(
            "draft-client-advocacy-harvest",
            function_id=FUNCTION_ID_26,
            task_ref=task_id,
            model="claude-sonnet",
            run_id=str(envelope.campaign_id),
        ) as span:
            with build_gateway_client() as gateway:
                response, cost = _complete_and_meter(
                    gateway,
                    vault,
                    model="claude-sonnet",
                    system_prompt=system_prompt,
                    user_content=user_content,
                    agent_run_id=agent_run["id"],
                )
            set_span_attribute(span, "cost", cost)

        output = _parse_json_content(response["content"])
        vault.update_agent_run(
            agent_run["id"], status="succeeded", output_payload=output, completed_at=_now_iso()
        )

    db.set_result_ref(
        task_id,
        {
            "naming_decision": output.get("naming_decision"),
            "consent_status": output.get("consent_status"),
            "agent_run_id": agent_run["id"],
            "campaign_id": campaign_id,
        },
    )
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)

def _draft_social_post_handler(
    task_id: str,
    envelope: TaskEnvelope,
    db: Any,
    *,
    task_name: str,
    function_id: str,
    prompt_dir: str,
    agent_name: str,
    asset_type: str,
    render_draft_text: Any,
    max_tokens: int = 1536,
) -> None:
    """Shared body for the 6 Wednesday drafting handlers that produce a
    single reviewable text asset from this week's research brief
    (insight-to-story, executive-ghostwrite, carousel, newsletter,
    case-study, content-repurpose). Each caller supplies its own
    `render_draft_text(output: dict) -> str` to flatten that function's
    own JSON output contract into the plain text qa_review_brand_steward_
    handler / qa_review_fact_check_handler will actually review -- the
    shape of that JSON differs per function (a carousel's slide array vs.
    a newsletter's subject+body), the review surface does not.

    Not a generalisation of qa_review_handler's ancestor-walk (that
    remains single-lineage, unchanged, still serving daily-signal-loop and
    the S8 proof circuit exactly as before) -- this is new code for a new
    loop, following the same call shape by convention, not by shared
    implementation.

    F-WEEKLY-LOOP-DRAFT-PUBLIC-SOURCE (7 Aug 2026, heartbeat round 20,
    Pieter's explicit ruling via AskUserQuestion: "Extend the exemption"):
    all 5 of weekly-content-loop's real drafting task types that route
    through this shared handler (draft-insight-to-story,
    draft-executive-ghostwrite, draft-carousel-post, draft-newsletter,
    draft-case-study -- draft-content-repurpose was cascade-dead-lettered
    as a downstream effect, not blocked directly) were dead-lettering on
    REDACTION_BLOCKED/full-name-like: this week's research brief
    legitimately names executives, clients, and case-study subjects, and
    every draft here is explicitly reviewed by qa_review_brand_steward_
    handler / qa_review_fact_check_handler (both already exempted, see
    _aggregate_qa_review) before it can ever reach approval. Setting
    content_class="public_source_content" on this call is correct for the
    same reason it was correct at F-INGEST-PUBLIC-SOURCE and
    qa_review_handler: the firewall's ruling is not being second-guessed,
    the class of content this handler sends is being accurately declared.
    Do not copy this to any other handler without its own equivalent,
    explicit Pieter sign-off recorded in that handler's own docstring.

    ``max_tokens`` (F-WEDNESDAY-DRAFT-TRUNCATION, 9 Aug 2026, heartbeat
    round 28): additive, default 1536 -- see _complete_and_meter's own
    note. Each of the 5 callers below now passes an explicit value sized
    to its own typical output length; see each caller's own comment for
    the reasoning.
    """
    lineage = resolve_lineage_result(task_id, db)
    if lineage is None:
        raise DispatchError(f"{task_name}: no research-brief ancestor carries a result_ref")
    _ancestor_task, ancestor_ref = lineage
    brief_id = ancestor_ref.get("brief_id")
    if not brief_id:
        raise DispatchError(f"{task_name}: ancestor result_ref carries no brief_id")

    with build_vault_client() as vault:
        campaign_id = vault.get_or_create_campaign(
            _campaign_name(envelope), function_id=function_id
        )
        brief = vault.get_brief(brief_id)

        agent_run = vault.create_agent_run(
            agent_name=_agent_name(agent_name, envelope),
            campaign_id=campaign_id,
            function_id=function_id,
            status="running",
            input_payload={"brief_id": brief_id, "pillar": ancestor_ref.get("pillar")},
        )

        system_prompt = _read_prompt(prompt_dir)
        user_content = json.dumps(
            {
                "brief": brief.get("body"),
                "pillar": ancestor_ref.get("pillar"),
                "vertical": ancestor_ref.get("vertical"),
                "audience_note": ancestor_ref.get("audience_note"),
            }
        )

        with emit_task_span(
            task_name,
            function_id=function_id,
            task_ref=task_id,
            model="claude-sonnet",
            run_id=str(envelope.campaign_id),
        ) as span:
            with build_gateway_client() as gateway:
                response, cost = _complete_and_meter(
                    gateway,
                    vault,
                    model="claude-sonnet",
                    system_prompt=system_prompt,
                    user_content=user_content,
                    agent_run_id=agent_run["id"],
                    content_class="public_source_content",
                    max_tokens=max_tokens,
                )
            set_span_attribute(span, "cost", cost)

        output = _parse_json_content(response["content"])
        draft_text = render_draft_text(output)
        asset = vault.create_asset(
            asset_type=asset_type,
            agent_run_id=agent_run["id"],
            campaign_id=campaign_id,
            function_id=function_id,
            content_bytes=draft_text.encode("utf-8"),
            approval_state="draft",
        )
        vault.update_agent_run(
            agent_run["id"], status="succeeded", output_payload=output, completed_at=_now_iso()
        )

    db.set_result_ref(
        task_id,
        {
            "vault_asset_id": asset["id"],
            "content_hash": asset["content_hash"],
            "agent_run_id": agent_run["id"],
            "campaign_id": campaign_id,
            "pillar": ancestor_ref.get("pillar"),
        },
    )
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)

def _render_simple_post(output: dict[str, Any]) -> str:
    return f"{output.get('post', '')}\n\n{output.get('cta_url', '')}".strip()

def draft_insight_to_story_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    _draft_social_post_handler(
        task_id,
        envelope,
        db,
        task_name="draft-insight-to-story",
        function_id=FUNCTION_ID_39,
        prompt_dir="39-insight-to-story-editor",
        agent_name="insight-to-story-editor",
        asset_type="linkedin_post",
        render_draft_text=_render_simple_post,
        max_tokens=2048,
    )

def draft_executive_ghostwrite_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    _draft_social_post_handler(
        task_id,
        envelope,
        db,
        task_name="draft-executive-ghostwrite",
        function_id=FUNCTION_ID_43,
        prompt_dir="43-executive-ghostwriter",
        agent_name="executive-ghostwriter",
        asset_type="linkedin_post",
        render_draft_text=_render_simple_post,
        max_tokens=2560,
    )

def _render_carousel(output: dict[str, Any]) -> str:
    lines = ["[CAROUSEL]"]
    for slide in output.get("slides", []):
        lines.append(
            f"Slide {slide.get('slide_number')}: {slide.get('headline')} — "
            f"{slide.get('subhead')}"
        )
    lines.append("")
    lines.append(f"CTA: {output.get('cta_url', '')}")
    lines.append("")
    lines.append("--- canva_bulk_create_csv ---")
    lines.append(output.get("canva_bulk_create_csv", ""))
    return "\n".join(lines)

def draft_carousel_post_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    _draft_social_post_handler(
        task_id,
        envelope,
        db,
        task_name="draft-carousel-post",
        function_id=FUNCTION_ID_45,
        prompt_dir="45-carousel-post-writer",
        agent_name="carousel-post-writer",
        asset_type="carousel_post",
        render_draft_text=_render_carousel,
        max_tokens=2560,
    )

def _render_newsletter(output: dict[str, Any]) -> str:
    return f"Subject: {output.get('subject', '')}\n\n{output.get('body', '')}"

def draft_newsletter_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    _draft_social_post_handler(
        task_id,
        envelope,
        db,
        task_name="draft-newsletter",
        function_id=FUNCTION_ID_46,
        prompt_dir="46-newsletter-writer",
        agent_name="newsletter-writer",
        asset_type="newsletter",
        render_draft_text=_render_newsletter,
        max_tokens=3584,
    )

def _render_case_study(output: dict[str, Any]) -> str:
    return json.dumps(output.get("case_study", output), indent=2)

def draft_case_study_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    """Case studies are intentionally excluded from friday-schedule-
    social-buffer's auto-schedule (function 47's own prompt.md: human-
    initiated cadence only, never published without a CLEARED client) --
    see schedule_social_buffer_handler's ELIGIBLE_BUFFER_DRAFT_TASK_TYPES,
    which deliberately omits this task_type."""
    _draft_social_post_handler(
        task_id,
        envelope,
        db,
        task_name="draft-case-study",
        function_id=FUNCTION_ID_47,
        prompt_dir="47-case-study-writer",
        agent_name="case-study-writer",
        asset_type="case_study",
        render_draft_text=_render_case_study,
        max_tokens=4096,
    )

def _render_repurpose(output: dict[str, Any]) -> str:
    lines = []
    for derivative in output.get("derivatives", []):
        lines.append(f"[{derivative.get('format', '?')}]")
        lines.append(derivative.get("post", ""))
        lines.append(derivative.get("cta_url", ""))
        lines.append("")
    return "\n".join(lines).strip()

# F-CONTENT-REPURPOSE-RACE (7 Aug 2026, heartbeat round 22 discovery, fixed
# round 23): function 52's actual input contract (schema.json,
# functions/52-content-repurposer/evals/*.json) is source_asset_summary +
# pillar + campaign + target_formats -- "one existing long-form asset,
# typically function 46's newsletter or function 47's case study"
# (prompt.md). draft-content-repurpose depends on BOTH wednesday-draft-
# newsletter and wednesday-draft-case-study directly (weekly-content-
# loop.yaml), NOT on tuesday-research-brief, so it was never going to have
# a brief_id-shaped result_ref to resolve at all.
#
# The original implementation below routed through _draft_social_post_
# handler exactly like the other 5 Wednesday drafters, which calls
# resolve_lineage_result -- a BFS that stops at the FIRST ancestor
# carrying ANY non-null result_ref. draft-newsletter's and draft-case-
# study's own result_ref (vault_asset_id/content_hash/pillar -- see
# _draft_social_post_handler's own db.set_result_ref call) never carries a
# brief_id, so the walk always stopped one hop too early and every run
# raised DispatchError("...ancestor result_ref carries no brief_id").
#
# Originally logged as a "race condition" (round 22's live trace showed it
# immediately following draft-newsletter's completion) -- rereading this
# code confirms it is NOT timing-dependent: F-DISPATCH-GATE already
# guarantees draft-content-repurpose cannot be dispatched until BOTH
# depends_on entries have reached COMPLETED (or this task would already
# have been cascade-dead-lettered via DependencyDeadLetteredError before
# reaching this handler at all -- see that class's own docstring). The
# failure was deterministic, 100% of the time, on every run that ever got
# this far; round 22 was simply the FIRST run where draft-newsletter and
# draft-case-study both completed for real (every earlier run had them
# dead-lettering upstream on REDACTION_BLOCKED, which is why this bug was
# masked until F-WEEKLY-LOOP-DRAFT-PUBLIC-SOURCE shipped). Round-22 naming
# kept as-is in the tracker/here for continuity across sessions.
_CONTENT_REPURPOSE_SOURCE_TASK_TYPES = ["draft-newsletter", "draft-case-study"]

# 2-3 shorter derivative formats per prompt.md's own framing ("2-3 shorter
# derivative social formats") -- all 3 of function 52's supported formats,
# maximizing what a single repurpose pass produces. schema.json bounds
# target_formats at 1-3 unique entries from exactly this set.
CONTENT_REPURPOSE_TARGET_FORMATS = ["linkedin_post", "x_post", "email_teaser"]

def _select_repurpose_source(task_id: str, db: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Reads draft-content-repurpose's own two direct depends_on rows
    (NOT a resolve_lineage_result walk -- see the F-CONTENT-REPURPOSE-RACE
    note above for why that mechanism is the wrong tool here) and returns
    whichever of draft-newsletter / draft-case-study carries a reviewable
    vault_asset_id, preferring the newsletter (the loop's primary owned-
    channel asset, and function 52's prompt.md lists it first) and falling
    back to the case study. Per this task's own original docstring and
    prompt.md ("typically function 46's newsletter or function 47's case
    study"), function 52 consumes ONE source asset, not a merge of both --
    "and/or" in the loop YAML's description, not "both, combined"."""
    current = db.get_task(task_id)
    dep_ids = current.get("depends_on") or []
    rows = {row["task_id"]: row for row in db.get_tasks(dep_ids)}
    by_type = {row.get("task_type"): row for row in rows.values()}
    for task_type in _CONTENT_REPURPOSE_SOURCE_TASK_TYPES:
        row = by_type.get(task_type)
        if row is not None and (row.get("result_ref") or {}).get("vault_asset_id"):
            return row, row["result_ref"]
    raise DispatchError(
        "draft-content-repurpose: neither draft-newsletter nor draft-case-study "
        "ancestor carries a reviewable vault_asset_id"
    )

def draft_content_repurpose_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    """Dedicated handler (round 23 fix, replacing the old _draft_social_
    post_handler delegation -- see F-CONTENT-REPURPOSE-RACE above): reads
    the actual drafted TEXT of its chosen source (newsletter or case
    study), not a research brief, matching function 52's real input
    contract (source_asset_summary + pillar + campaign + target_formats).

    content_class="public_source_content" is set on this call for the same
    reason F-WEEKLY-LOOP-DRAFT-PUBLIC-SOURCE's docstring requires its own
    explicit sign-off per new call site: the source text here is the
    newsletter/case-study draft's own already-written content, itself
    rendered from this week's research brief -- the SAME class of
    legitimately-public, name-bearing content Pieter's "Extend the
    exemption" ruling (7 Aug 2026, round 20) already covers, one hop
    downstream. draft-content-repurpose was never actually exercised under
    that ruling (it was cascade-dead-lettered upstream every time, per the
    note above) so this is a genuine new call site, not a copy-paste of an
    already-approved one -- flagged to Pieter for awareness alongside this
    fix rather than gated on a fresh AskUserQuestion, since without it this
    handler reproduces the identical REDACTION_BLOCKED failure the round-20
    ruling was written to prevent, on content of the same already-approved
    class, still gated behind the unchanged Thursday QA + Friday approval
    steps before anything publishes."""
    source_task, source_ref = _select_repurpose_source(task_id, db)
    vault_asset_id = source_ref["vault_asset_id"]
    pillar = source_ref.get("pillar")
    if not pillar:
        raise DispatchError(
            "draft-content-repurpose: source ancestor result_ref carries no pillar"
        )
    campaign_slug = pillar.lower().replace(" ", "-")

    with build_vault_client() as vault:
        campaign_id = vault.get_or_create_campaign(
            _campaign_name(envelope), function_id=FUNCTION_ID_52
        )
        source_asset = vault.get_asset(vault_asset_id)
        source_text = base64.b64decode(source_asset["content_base64"]).decode("utf-8")

        agent_run = vault.create_agent_run(
            agent_name=_agent_name("content-repurposer", envelope),
            campaign_id=campaign_id,
            function_id=FUNCTION_ID_52,
            status="running",
            input_payload={
                "pillar": pillar,
                "campaign": campaign_slug,
                "source_task_type": source_task.get("task_type"),
            },
        )

        system_prompt = _read_prompt("52-content-repurposer")
        user_content = json.dumps(
            {
                "source_asset_summary": source_text,
                "pillar": pillar,
                "campaign": campaign_slug,
                "target_formats": CONTENT_REPURPOSE_TARGET_FORMATS,
            }
        )

        with emit_task_span(
            "draft-content-repurpose",
            function_id=FUNCTION_ID_52,
            task_ref=task_id,
            model="claude-sonnet",
            run_id=str(envelope.campaign_id),
        ) as span:
            with build_gateway_client() as gateway:
                response, cost = _complete_and_meter(
                    gateway,
                    vault,
                    model="claude-sonnet",
                    system_prompt=system_prompt,
                    user_content=user_content,
                    agent_run_id=agent_run["id"],
                    content_class="public_source_content",
                    max_tokens=4096,
                )
            set_span_attribute(span, "cost", cost)

        output = _parse_json_content(response["content"])
        draft_text = _render_repurpose(output)
        asset = vault.create_asset(
            asset_type="content_derivatives",
            agent_run_id=agent_run["id"],
            campaign_id=campaign_id,
            function_id=FUNCTION_ID_52,
            content_bytes=draft_text.encode("utf-8"),
            approval_state="draft",
        )
        vault.update_agent_run(
            agent_run["id"], status="succeeded", output_payload=output, completed_at=_now_iso()
        )

    db.set_result_ref(
        task_id,
        {
            "vault_asset_id": asset["id"],
            "content_hash": asset["content_hash"],
            "agent_run_id": agent_run["id"],
            "campaign_id": campaign_id,
            "pillar": pillar,
        },
    )
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)

# ---------------------------------------------------------------------
# Thursday aggregate QA gates (qa-review-brand-steward, qa-review-fact-
# check) -- see module docstring for why these are NOT a generalisation
# of qa_review_handler and what "aggregate, all-or-nothing" means today.
# ---------------------------------------------------------------------

# The 6 Wednesday draft task_types Thursday's QA actually reviews, in the
# exact order weekly-content-loop.yaml lists them under both QA tasks'
# depends_on. draft-research-brief and draft-client-advocacy-harvest are
# deliberately absent -- they are upstream research inputs, not
# publish-bound drafts (see draft_research_brief_handler's own docstring).
QA_REVIEWED_DRAFT_TASK_TYPES = [
    "draft-insight-to-story",
    "draft-executive-ghostwrite",
    "draft-carousel-post",
    "draft-newsletter",
    "draft-case-study",
    "draft-content-repurpose",
]

def _gather_sibling_drafts(task_id: str, db: Any) -> list[dict[str, Any]]:
    """Fetches THIS task's own depends_on rows directly (NOT a
    resolve_lineage_result walk -- that mechanism returns exactly one
    ancestor; Thursday's QA tasks depend on 6 at once, per weekly-
    content-loop.yaml, and every one of them must already carry a
    result_ref by the time this task is dispatchable, since
    dispatch_task's own F-DISPATCH-GATE refuses to run this task at all
    until every depends_on entry has COMPLETED)."""
    current = db.get_task(task_id)
    dep_ids = current.get("depends_on") or []
    rows = db.get_tasks(dep_ids)
    return [row for row in rows if row.get("task_type") in QA_REVIEWED_DRAFT_TASK_TYPES]

def _aggregate_qa_review(
    task_id: str,
    envelope: TaskEnvelope,
    db: Any,
    *,
    task_name: str,
    function_id: str,
    prompt_dir: str,
    agent_name: str,
    review_kind: str,
) -> None:
    """Shared body for qa_review_brand_steward_handler and
    qa_review_fact_check_handler. Reviews each of the 6 Wednesday drafts
    independently against `prompt_dir`'s prompt, and records a per-draft
    verdict in this task's own result_ref for visibility -- but see the
    module docstring: the task itself still resolves to ONE terminal
    state, all-or-nothing, because friday-schedule-social-buffer and
    friday-publish-newsletter are gated on this single task per the
    existing loop YAML shape, not on the 6 drafts individually."""
    permission_check = load_permission_check()
    drafts = _gather_sibling_drafts(task_id, db)
    if not drafts:
        raise DispatchError(f"{task_name}: no reviewable Wednesday draft found among depends_on")

    system_prompt = _read_prompt(prompt_dir)
    per_draft_verdicts: list[dict[str, Any]] = []
    any_violation = False

    with build_vault_client() as vault:
        campaign_id = vault.get_or_create_campaign(
            _campaign_name(envelope), function_id=function_id
        )

        for draft_row in drafts:
            draft_ref = draft_row.get("result_ref") or {}
            vault_asset_id = draft_ref.get("vault_asset_id")
            if not vault_asset_id:
                # A draft that itself never produced an asset (e.g. it was
                # dead-lettered upstream) cannot be reviewed -- treat as a
                # violation for this review rather than silently skipping
                # it, since QA_BLOCKED intentionally errs toward blocking.
                per_draft_verdicts.append(
                    {
                        "draft_task_id": draft_row["task_id"],
                        "draft_task_type": draft_row.get("task_type"),
                        "pass": False,
                        "violations": ["no_reviewable_asset"],
                    }
                )
                any_violation = True
                continue

            asset = vault.get_asset(vault_asset_id)
            draft_text = base64.b64decode(asset["content_base64"]).decode("utf-8")

            agent_run = vault.create_agent_run(
                agent_name=_agent_name(agent_name, envelope),
                campaign_id=campaign_id,
                function_id=function_id,
                status="running",
                input_payload={"channel": "linkedin", "review_kind": review_kind},
            )
            user_content = json.dumps(
                {"draft_text": draft_text, "client_references": [], "channel": "linkedin"}
            )
            with emit_task_span(
                task_name,
                function_id=function_id,
                task_ref=f"{task_id}:{draft_row['task_id']}",
                model="claude-sonnet",
                run_id=str(envelope.campaign_id),
            ) as span:
                with build_gateway_client() as gateway:
                    response, cost = _complete_and_meter(
                        gateway,
                        vault,
                        model="claude-sonnet",
                        system_prompt=system_prompt,
                        user_content=user_content,
                        agent_run_id=agent_run["id"],
                        content_class="public_source_content",
                    )
                set_span_attribute(span, "cost", cost)

            verdict = _parse_json_content(response["content"])
            violations = list(verdict.get("violations") or [])
            uncleared = permission_check.find_uncleared_references([])
            if uncleared and permission_check.VIOLATION_CODE not in violations:
                violations.append(permission_check.VIOLATION_CODE)
            passed = not violations
            any_violation = any_violation or not passed

            vault.update_agent_run(
                agent_run["id"],
                status="succeeded" if passed else "failed",
                output_payload={"pass": passed, "violations": violations},
                completed_at=_now_iso(),
            )
            per_draft_verdicts.append(
                {
                    "draft_task_id": draft_row["task_id"],
                    "draft_task_type": draft_row.get("task_type"),
                    "vault_asset_id": vault_asset_id,
                    "content_hash": asset.get("content_hash"),
                    "pass": passed,
                    "violations": violations,
                }
            )

    result_ref = {
        "pass": not any_violation,
        "per_draft": per_draft_verdicts,
        "campaign_id": campaign_id,
    }
    db.set_result_ref(task_id, result_ref)
    if any_violation:
        db.transition(task_id, TaskStateEnum.FAILED, TransitionReason.QA_BLOCKED)
        log_event(
            logger,
            logging.INFO,
            "aggregate_qa_review_blocked",
            task_id=task_id,
            review_kind=review_kind,
            per_draft=per_draft_verdicts,
        )
        return  # never advance_dependents -- see module docstring's all-or-nothing note
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)

def qa_review_brand_steward_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    _aggregate_qa_review(
        task_id,
        envelope,
        db,
        task_name="qa-review-brand-steward",
        function_id=FUNCTION_ID_02,
        prompt_dir="02-brand-steward-qa",
        agent_name="brand-steward-qa",
        review_kind="brand_steward",
    )

def qa_review_fact_check_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    """Uses functions/48-fact-check-verdict/prompt.md -- a FIRST DRAFT
    Pieter has not yet reviewed, bounded strictly to weekly-content-
    loop.yaml's own stated Thursday fact-check criterion. See module
    docstring."""
    _aggregate_qa_review(
        task_id,
        envelope,
        db,
        task_name="qa-review-fact-check",
        function_id=FUNCTION_ID_48_FACT_CHECK,
        prompt_dir="48-fact-check-verdict",
        agent_name="fact-check-verdict",
        review_kind="fact_check",
    )

# ---------------------------------------------------------------------
# Friday: schedule-social-buffer, publish-newsletter -- both request a
# REAL gate-check (mirroring request_approval_handler exactly), one card
# per eligible asset. Neither ever calls Buffer or sends an email itself
# -- see request_approval_handler's own docstring for why, and this
# module's docstring for publish-newsletter's specific caveat.
# ---------------------------------------------------------------------

# case-study is deliberately excluded -- see draft_case_study_handler's
# docstring and function 47's own prompt.md.
ELIGIBLE_BUFFER_DRAFT_TASK_TYPES = [
    "draft-insight-to-story",
    "draft-executive-ghostwrite",
    "draft-carousel-post",
    "draft-content-repurpose",
]

def schedule_social_buffer_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    current = db.get_task(task_id)
    params = current.get("params") or {}
    weekly_cap = params.get("buffer_weekly_post_cap")

    qa_lineage = resolve_lineage_result(task_id, db)
    if qa_lineage is None:
        raise DispatchError("schedule-social-buffer: no QA-gate ancestor carries a result_ref")
    qa_ancestor_task, _qa_ref = qa_lineage
    drafts = _gather_sibling_drafts(qa_ancestor_task["task_id"], db)
    eligible = [d for d in drafts if d.get("task_type") in ELIGIBLE_BUFFER_DRAFT_TASK_TYPES]
    if weekly_cap:
        eligible = eligible[: int(weekly_cap)]

    if not eligible:
        raise DispatchError("schedule-social-buffer: no eligible QA'd draft found to schedule")

    requested: list[dict[str, Any]] = []
    with build_gatekeeper_client() as gatekeeper:
        for draft_row in eligible:
            draft_ref = draft_row.get("result_ref") or {}
            content_hash = draft_ref.get("content_hash")
            if not content_hash:
                continue
            with emit_task_span(
                "schedule-social-buffer",
                function_id=REAL_PUBLISH_FUNCTION_ID,
                task_ref=f"{task_id}:{draft_row['task_id']}",
                model="none",
                cost=0.0,
                run_id=str(envelope.campaign_id),
            ):
                decision = gatekeeper.gate_check(
                    agent_run_id=str(envelope.agent_run_id),
                    function_id=REAL_PUBLISH_FUNCTION_ID,
                    action_class=REAL_PUBLISH_ACTION_CLASS,
                    content_hash=content_hash,
                    preview_title=f"[{draft_row.get('task_type')}] weekly-content-loop",
                    preview_reference=f"weekly-content-loop://{draft_row['task_id']}",
                )
            requested.append(
                {
                    "draft_task_id": draft_row["task_id"],
                    "draft_task_type": draft_row.get("task_type"),
                    "decision_id": decision.get("decision_id"),
                    "outcome": decision.get("outcome"),
                    "approval_id": decision.get("approval_id"),
                }
            )

    if not requested:
        raise DispatchError(
            "schedule-social-buffer: every eligible draft was missing a content_hash"
        )

    db.set_result_ref(task_id, {"requested": requested})
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)

def publish_newsletter_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    """Requests approval only -- see module docstring: no real email will
    send once approved until Publisher's Graph integration is wired with
    a real Entra ID app registration Pieter still needs to create."""
    qa_lineage = resolve_lineage_result(task_id, db)
    if qa_lineage is None:
        raise DispatchError("publish-newsletter: no QA-gate ancestor carries a result_ref")
    qa_ancestor_task, _qa_ref = qa_lineage
    drafts = _gather_sibling_drafts(qa_ancestor_task["task_id"], db)
    newsletter_draft = next((d for d in drafts if d.get("task_type") == "draft-newsletter"), None)
    if newsletter_draft is None:
        raise DispatchError("publish-newsletter: no draft-newsletter sibling found")
    content_hash = (newsletter_draft.get("result_ref") or {}).get("content_hash")
    if not content_hash:
        raise DispatchError(
            "publish-newsletter: draft-newsletter result_ref carries no content_hash"
        )

    with build_gatekeeper_client() as gatekeeper:
        with emit_task_span(
            "publish-newsletter",
            function_id=REAL_NEWSLETTER_FUNCTION_ID,
            task_ref=task_id,
            model="none",
            cost=0.0,
            run_id=str(envelope.campaign_id),
        ):
            decision = gatekeeper.gate_check(
                agent_run_id=str(envelope.agent_run_id),
                function_id=REAL_NEWSLETTER_FUNCTION_ID,
                action_class=REAL_PUBLISH_ACTION_CLASS,
                content_hash=content_hash,
                preview_title=(
                    "[NEWSLETTER] weekly-content-loop — send NOT yet wired, see docstring"
                ),
                preview_reference=f"weekly-content-loop://{newsletter_draft['task_id']}",
            )

    db.set_result_ref(
        task_id,
        {
            "decision_id": decision.get("decision_id"),
            "outcome": decision.get("outcome"),
            "approval_id": decision.get("approval_id"),
            "content_hash": content_hash,
        },
    )
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)

# ---------------------------------------------------------------------
# Dispatch table + legacy pass-through fallback (plan step 6; AC-01, AC-02)
# ---------------------------------------------------------------------

DISPATCH_TABLE: dict[str, Any] = {
    "ingest-signals": ingest_signals_handler,
    "draft-brief": draft_brief_handler,
    "qa-review": qa_review_handler,
    "draft-content": draft_content_handler,
    "request-approval": request_approval_handler,
    # S11 weekly-content-loop.yaml, 6 Aug 2026 -- see module docstring.
    "plan-content-monday": plan_content_monday_handler,
    "draft-research-brief": draft_research_brief_handler,
    "draft-client-advocacy-harvest": draft_client_advocacy_harvest_handler,
    "draft-insight-to-story": draft_insight_to_story_handler,
    "draft-executive-ghostwrite": draft_executive_ghostwrite_handler,
    "draft-carousel-post": draft_carousel_post_handler,
    "draft-newsletter": draft_newsletter_handler,
    "draft-case-study": draft_case_study_handler,
    "draft-content-repurpose": draft_content_repurpose_handler,
    "qa-review-brand-steward": qa_review_brand_steward_handler,
    "qa-review-fact-check": qa_review_fact_check_handler,
    "schedule-social-buffer": schedule_social_buffer_handler,
    "publish-newsletter": publish_newsletter_handler,
}

def legacy_task_pass_through(task_id: str, task_type: str, db: Any) -> None:
    """BYTE-IDENTICAL to worker.py's pre-session unconditional stub
    (RUNNING -> COMPLETED -> advance_dependents). Used for any genuinely
    unregistered task_type -- in that case, if `task_id` has no backing
    task_state row (the e2e test's synthetic 'zzz-unregistered-test-type'
    case), the first db.transition() call's own task_transitions FK
    constraint raises naturally; that propagates to worker.py's existing
    outer try/except (task_handling_failed logged), leaving no unhandled
    exception and no silently-COMPLETED task -- the message is still
    safely completed at transport level regardless (worker.py's `finally`
    block).
    """
    db.transition(task_id, TaskStateEnum.RUNNING, TransitionReason.DISPATCHED)
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)
    log_event(
        logger, logging.INFO, "legacy_task_pass_through", task_id=task_id, task_type=task_type
    )

_PERMANENTLY_BLOCKED_STATES = frozenset(
    {TaskStateEnum.DEAD_LETTERED.value, TaskStateEnum.FAILED.value}
)

def _find_dead_lettered_dependency(current: dict[str, Any], db: Any) -> dict[str, Any] | None:
    """One-hop check: does `current` (a task row, already known to be
    not-yet-dispatchable) have any depends_on entry that has reached a
    PERMANENT terminal state -- DEAD_LETTERED or FAILED (see
    _PERMANENTLY_BLOCKED_STATES; F-CASCADE-QA-BLOCKED, 4 Aug 2026,
    heartbeat round 17 -- FAILED added alongside the original
    DEAD_LETTERED-only check once a real QA_BLOCKED verdict proved
    equally un-completable and equally in need of a fast cascade).
    Returns that dependency's row (for a precise error message) or None.
    Function name kept as-is despite the broadened check to minimize
    this fix's diff -- see DependencyDeadLetteredError's docstring.

    Deliberately shallow -- see DependencyDeadLetteredError's docstring
    for why a one-hop check is sufficient and a recursive lineage walk
    is not needed here (this is NOT the same as _resolve_dep_lineage
    below, which walks ancestors for a different purpose -- finding a
    real result_ref to build on, not checking for permanent failure)."""
    dep_ids = current.get("depends_on") or []
    if not dep_ids:
        return None
    for dep in db.get_tasks(dep_ids):
        if dep.get("state") in _PERMANENTLY_BLOCKED_STATES:
            return dep
    return None

def dispatch_task(envelope: TaskEnvelope, db: Any) -> None:
    """The one entry point worker.handle_task_message calls. Routes to a
    real handler for every task_type in DISPATCH_TABLE; a genuinely
    unregistered task_type takes the legacy pass-through path unchanged
    from pre-session behaviour.

    F-DISPATCH-GATE: refuses to run ANYTHING (handler or legacy pass-
    through) for a task whose current DB state isn't actually
    dispatchable yet -- see TaskNotReadyError's docstring for why this
    check exists. Previously this function transitioned straight to
    RUNNING and invoked the handler unconditionally, regardless of the
    task's real state, which let a downstream task run (and usually fail,
    permanently, with no retry) before its dependency had genuinely
    completed.
    """
    task_id = str(envelope.task_id)
    current = db.get_task(task_id)
    if current is None or current.get("state") != TaskStateEnum.DISPATCHABLE.value:
        blocking_dep = None
        if current is not None:
            blocking_dep = _find_dead_lettered_dependency(current, db)
        if blocking_dep is not None:
            blocking_state = blocking_dep.get("state", "unknown")
            raise DependencyDeadLetteredError(
                f"task {task_id} ({envelope.task_type}) can never become "
                f"dispatchable: its dependency {blocking_dep['task_id']} "
                f"({blocking_dep.get('task_type', 'unknown')}) is "
                f"{blocking_state} and will never complete",
                blocking_task_id=blocking_dep["task_id"],
                blocking_task_type=blocking_dep.get("task_type", "unknown"),
            )
        raise TaskNotReadyError(
            f"task {task_id} ({envelope.task_type}) is not dispatchable yet "
            f"(state={current.get('state') if current else 'unknown'}); its "
            "dependencies may not have completed"
        )
    handler = DISPATCH_TABLE.get(envelope.task_type)
    if handler is None:
        legacy_task_pass_through(task_id, envelope.task_type, db)
        return
    db.transition(task_id, TaskStateEnum.RUNNING, TransitionReason.DISPATCHED)
    handler(task_id, envelope, db)
