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
  - qa-review-brand-steward and qa-review-fact-check were originally
    AGGREGATE gates (thursday-brand-steward-qa depended_on all 6
    Wednesday drafts at once, reviewed each independently, but resolved
    to ONE terminal state for the whole task -- so a single violation in
    any one of the 6 blocked friday-schedule-social-buffer and friday-
    publish-newsletter for every draft, including ones that individually
    passed both reviews cleanly). ROUND 34 (docs/content-learnings.md,
    the "batch-gating" finding, confirmed live the night of 10 Aug 2026):
    restructured to a true per-draft graph -- weekly-content-loop.yaml
    now has ONE Thursday review task per Wednesday draft per review_kind
    (12 total), each depends_on exactly one Wednesday draft, handled by
    _single_draft_qa_review (a close structural sibling of the existing
    single-ancestor qa_review_handler, not a generalisation of it -- see
    that function's own docstring for why they stay separate). A
    violation in one draft now dead-letters only that draft's own Friday
    task(s); every other draft's Thursday/Friday tasks are unaffected,
    since the dependency graph -- not any handler-level filtering -- is
    what provides the isolation.
  - qa-review-fact-check reuses the SAME per-draft QA mechanism as
    brand-steward, against a NEW prompt (functions/48-fact-check-verdict/
    prompt.md) that Pieter has NOT fully signed off on yet -- it is
    bounded strictly to weekly-content-loop.yaml's own stated Thursday
    fact-check criterion ("confirms every proof point traces to a cited
    source, no fabricated claim survives downstream") and invents no
    policy beyond that, but it is a first draft, not an approved QA
    policy, and should be treated as such until Pieter has read it.
  - friday-schedule-social-buffer requests a REAL gate-check (function_id
    publish.social_post, mirroring request_approval_handler exactly) for
    each Wednesday draft eligible for Buffer scheduling. ROUND 34: this is
    now 4 separate per-draft tasks (one per eligible draft type -- see
    weekly-content-loop.yaml), each gated on that one draft's own 2
    Thursday review tasks, rather than one task iterating a batch capped
    at buffer_weekly_post_cap -- the weekly cap concept no longer applies
    at this granularity (case-study is still explicitly excluded per
    function 47's own prompt.md -- human-initiated cadence only). Each
    draft gets its OWN approval card, not one combined card, since
    gate_check's contract is one content_hash per call.
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
import difflib
import hashlib
import html as html_module
import importlib.util
import json
import logging
import re
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlparse

import yaml
from jsonschema import Draft202012Validator
from telemetry_lib import set_span_attribute

from orchestrator import brand_rules
from orchestrator.clients.gatekeeper_client import GatekeeperClient, resolve_gatekeeper_base_url
from orchestrator.clients.gateway_client import (
    GatewayClientError,
    OrchestratorGatewayClient,
    resolve_gateway_base_url,
)
from orchestrator.clients.mcp_client import MCPClient, resolve_mcp_web_base_url
from orchestrator.clients.publisher_client import PublisherClient, resolve_publisher_base_url
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

class TaskAlreadyTerminalError(RuntimeError):
    """Raised by dispatch_task instead of TaskNotReadyError when task_id's
    OWN current state has already reached a terminal state (COMPLETED,
    DEAD_LETTERED, or FAILED) rather than merely not-yet-dispatchable.

    F-DUPLICATE-TERMINAL-REQUEUE (11 Aug 2026, closes the round-23
    finding): at-least-once queue delivery -- Service Bus redelivery
    after a lock/visibility-timeout lapse, or, per TaskNotReadyError's own
    docstring, more than one orchestrator replica independently polling
    the same queue -- can deliver a SECOND copy of a task's own message
    after the first copy already ran it to completion (or dead-lettered
    it, or QA-blocked it). Before this fix, dispatch_task's not-ready gate
    could not tell "my dependencies haven't finished yet, and WILL"
    (current.state not yet DISPATCHABLE, worth waiting on) apart from "I
    MYSELF already finished, and never will change again" (current.state
    is COMPLETED/DEAD_LETTERED/FAILED) -- both fell into the same
    `current.state != DISPATCHABLE` branch and both raised
    TaskNotReadyError. A duplicate message for an already-terminal task
    would then requeue NOT_READY_MAX_REQUEUES (20) times for no reason
    (nothing is ever going to change), hit worker.py's
    task_not_ready_giving_up path, and call state_machine.record_failure
    -- which was idempotent against redelivery landing on an already-
    DEAD_LETTERED task (OR-001), but NOT against one that's already
    COMPLETED or FAILED: it would increment retry_count and transition
    the task straight back to RETRY_PENDING, silently overwriting a
    genuinely-finished task's terminal state in the DB with no obvious
    external symptom (the queue message itself is discarded either way,
    per run_worker_loop's unconditional finally-complete) -- a real
    state-corruption bug hiding behind what looked, from the outside,
    like a harmless no-op. (state_machine.record_failure's own
    idempotency guard is now broadened to cover this directly too, as a
    second layer -- see its docstring -- but the fix here is what stops
    the ~15-minute, 20-requeue detour from ever starting.)

    worker.handle_task_message catches this and treats it as exactly what
    it is: an idempotent duplicate. No requeue, no retry, no dead-letter,
    no state_machine call at all -- the task's state is already final and
    correct; the only thing left to do is log it and discard the
    redundant message."""

    def __init__(self, message: str, current_state: str) -> None:
        super().__init__(message)
        self.current_state = current_state

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

def build_publisher_client() -> PublisherClient:
    return PublisherClient(base_url=resolve_publisher_base_url())

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

# F-A / F-E (scan-market fixes, this change). Two gaps this section closes,
# both of which previously let a scan REPORT success while producing
# something that could not satisfy its own stated contract:
#
#   F-A -- the model's output was json.loads'd and written straight to the
#          Vault. functions/09-market-intelligence-director/schema.json
#          existed and was correct, but nothing called it at runtime: the
#          "at least 3 signals, https attribution, five-pillar enum,
#          honest confidence" contract was prompt text plus CI evals
#          against a deterministic MOCK. A live run returning one
#          unattributed signal wrote a signals row and COMPLETED.
#   F-E -- a failed fetch was a warning and a redaction block dropped a
#          source, so the task succeeded on ONE surviving source, which
#          structurally cannot satisfy prompt.md's own "at least 2
#          distinct domains" rule. Nothing recorded that the day's scan
#          had run on 1 of 4 sources.
#
# Both now fail closed, consistent with this codebase's existing default
# (unlisted autonomy pair -> blocked; unlisted client -> blocked; failed
# Vault lookup -> refuse). A scan that cannot meet its contract must not
# write a signals row that downstream briefs will cite as evidence.


DEFAULT_MIN_INGEST_SOURCES = 2
DEFAULT_MIN_INGEST_DOMAINS = 2


def _ingest_floors(sources: dict[str, Any]) -> tuple[int, int]:
    """Minimum surviving sources and distinct domains for a scan to count.

    Read from scan-profiles.yaml rather than hardcoded here, mirroring
    routing.yaml/budgets.yaml's policy-as-data convention -- relaxing a
    floor (or raising it once a profile has more sources) is one reviewed
    YAML line, not a code change and redeploy.
    """
    return (
        int(sources.get("min_sources", DEFAULT_MIN_INGEST_SOURCES)),
        int(sources.get("min_distinct_domains", DEFAULT_MIN_INGEST_DOMAINS)),
    )


def _ingest_source_chars(sources: dict[str, Any]) -> int:
    """Per-source evidence budget, read from scan-profiles.yaml for the
    same reason the floors are (see _ingest_floors)."""
    return int(sources.get("source_chars", DEFAULT_INGEST_SOURCE_CHARS))


def _distinct_domains(urls: list[str]) -> set[str]:
    """Hostnames, lowercased. Two feeds on one host are one domain -- the
    same reading prompt.md's domain-diversity rule uses ("three headlines
    from one vendor blog is one signal, not three"), and the reason the
    floor is checked on domains and not only on source count:
    the market-intelligence profile's 4 URLs span only 3 hosts."""
    return {(urlparse(url).hostname or "").lower() for url in urls if url}


def _assert_ingest_floor(
    stage: str, urls: list[str], min_sources: int, min_domains: int
) -> None:
    """Raise DispatchError when `urls` is below either floor.

    Called twice per run against the same floors: once on what fetch_url
    actually returned (before any model spend), and once on what survived
    the redaction fallback's source-dropping (after it, since that loop
    can take a passing set below the floor). `stage` names which, so the
    failure says where the sources were lost.
    """
    domains = _distinct_domains(urls)
    if len(urls) >= min_sources and len(domains) >= min_domains:
        return
    raise DispatchError(
        f"ingest-signals: {stage} left {len(urls)} source(s) across "
        f"{len(domains)} domain(s), below the floor of {min_sources} source(s) / "
        f"{min_domains} domain(s) -- a scan below this floor cannot satisfy "
        "function 09's own at-least-2-distinct-domains rule, so it is failed "
        "rather than written to the Vault as if it were a complete scan"
    )


def _load_function_input_schema(function_id: str) -> dict[str, Any]:
    """The `input` subschema of a function package's own schema.json."""
    path = functions_dir() / function_id / "schema.json"
    schema = json.loads(path.read_text(encoding="utf-8"))
    input_schema = schema.get("properties", {}).get("input")
    if not isinstance(input_schema, dict):
        raise DispatchError(
            f"{function_id}: schema.json carries no properties.input subschema to validate against"
        )
    return input_schema


def _validate_function_input(function_id: str, payload: Any) -> None:
    """Validate what a handler is about to SEND against the function's own
    input contract (F-INPUT-UNVALIDATED).

    The output side got this in the F-A commit; the input side had the
    identical hole, and it hid a worse bug. Every one of the eight weekly
    drafting handlers was sending a payload its own schema.json would
    reject -- most consequentially function 41, the Research Brief Writer,
    which received `{"pillar": ...}` alone while its schema requires
    `signal_summary`, the field whose own description reads "the raw
    signal or opportunity-card text this brief is built from... a brief
    must never invent evidence the signal does not supply". A function
    asked for citations, handed no sources, and nothing anywhere noticed.

    Validating the input is what stops a handler and its package drifting
    apart again silently: the schema stops being documentation and starts
    being the wire format.
    """
    validator = Draft202012Validator(_load_function_input_schema(function_id))
    errors = sorted(validator.iter_errors(payload), key=lambda err: list(err.absolute_path))
    if not errors:
        return
    first = errors[0]
    location = "/".join(str(part) for part in first.absolute_path) or "<root>"
    raise DispatchError(
        f"{function_id}: handler input failed schema.json validation at {location} "
        f"({len(errors)} violation(s)): {first.message[:200]}"
    )


def _load_function_output_schema(function_id: str) -> dict[str, Any]:
    """The `output` subschema of a function package's own schema.json.

    Resolved through functions_dir() at call time, never cached at module
    import -- same reason _load_scan_profiles() and _read_prompt() do
    (see config.functions_dir()'s docstring)."""
    path = functions_dir() / function_id / "schema.json"
    schema = json.loads(path.read_text(encoding="utf-8"))
    output_schema = schema.get("properties", {}).get("output")
    if not isinstance(output_schema, dict):
        raise DispatchError(
            f"{function_id}: schema.json carries no properties.output subschema to validate against"
        )
    return output_schema


def _validate_function_output(function_id: str, output: Any) -> None:
    """Validate a parsed model output against its own package's schema.

    The violation message is truncated to telemetry_lib's MAX_TEXT_LEN
    (200) before it reaches the exception text: jsonschema echoes the
    offending value, and that value is model output derived from fetched
    news bodies -- exactly the content class the rest of this pipeline is
    careful not to spill into logs wholesale.
    """
    validator = Draft202012Validator(_load_function_output_schema(function_id))
    errors = sorted(validator.iter_errors(output), key=lambda err: list(err.absolute_path))
    if not errors:
        return
    first = errors[0]
    location = "/".join(str(part) for part in first.absolute_path) or "<root>"
    raise DispatchError(
        f"{function_id}: model output failed schema.json validation at {location} "
        f"({len(errors)} violation(s)): {first.message[:200]}"
    )


def _assert_signal_domain_floor(
    output: dict[str, Any], min_domains: int, available_domains: int | None = None
) -> None:
    """Enforce prompt.md hard rule 3 -- the one contract rule schema.json
    structurally cannot express, since JSON Schema cannot say "the set of
    hostnames across this array has at least N members". Without this, a
    batch citing one domain three times passes validation and reaches the
    Vault looking like three corroborated signals.

    The floor is capped at what retrieval ACTUALLY delivered
    (`available_domains`). A model cannot cite two domains when only one
    resolved, so on a degraded day the uncapped floor would fail a batch
    for a shortfall it had no way to avoid -- punishing the scan for the
    fetch layer's bad morning. Capping keeps rule 3 fully enforced
    whenever it is satisfiable, which is the only time enforcing it says
    anything, and is what makes relaxing the RETRIEVAL floor a decision
    about how many sources a scan needs rather than a quiet weakening of
    what the model is held to."""
    effective = min_domains if available_domains is None else min(min_domains, available_domains)
    cited = [str(item.get("source_url", "")) for item in _batch_items(output)]
    domains = _distinct_domains(cited)
    if len(domains) >= effective:
        return
    raise DispatchError(
        f"ingest-signals: emitted signals cite {len(domains)} distinct domain(s), "
        f"below the effective floor of {effective} (configured {min_domains}, "
        f"{available_domains} retrieved) -- prompt.md hard rule 3 "
        "(schema.json cannot express a cross-item uniqueness constraint)"
    )


SCAN_PROFILES_PATH = ("_shared", "scan-profiles.yaml")
DEFAULT_SCAN_PROFILE_ID = "market-intelligence"


def _load_scan_profiles() -> dict[str, Any]:
    """functions/_shared/scan-profiles.yaml, resolved through
    functions_dir() at call time (see config.functions_dir()'s docstring
    for why nothing here resolves a path at module import).

    Replaced functions/09-market-intelligence-director/fetch_sources.yaml
    (F-SCAN-PROFILE-SINGLETON): that file described ONE scan -- `topic`
    and `horizon_days` were scalars at the root -- while eleven further
    scanner packages sat complete-but-unwired with nowhere to say what
    each of them scans. It also lived inside function 09's package while
    describing work for twelve functions."""
    path = functions_dir().joinpath(*SCAN_PROFILES_PATH)
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _resolve_scan_profile(profile_id: str, *, require_urls: bool = True) -> dict[str, Any]:
    """One profile, with `defaults` merged underneath its own keys.

    Refuses, rather than degrades, in both failure cases:

      * an unknown profile_id -- a typo in a loop YAML's params must not
        silently fall back to scanning the wrong market;
      * a profile with no `urls` -- a scan of nothing is not a scan. The
        error names the file and the profile so the fix is obvious from
        the failure alone.

    `require_urls=False` is for the eleven fan-out scanners, which are
    deliberately sourceless today (see the profiles file's own header).
    Those complete as not-configured rather than failing, so eleven
    unfilled profiles do not make a red daily loop the normal state --
    see _make_scanner_handler.
    """
    document = _load_scan_profiles()
    profiles = {entry["profile_id"]: entry for entry in document.get("profiles", [])}
    profile = profiles.get(profile_id)
    if profile is None:
        raise DispatchError(
            f"scan profile {profile_id!r} is not defined in "
            f"functions/{'/'.join(SCAN_PROFILES_PATH)} "
            f"(defined: {', '.join(sorted(profiles))})"
        )
    resolved = {**document.get("defaults", {}), **profile}
    if require_urls and not resolved.get("urls"):
        raise DispatchError(
            f"scan profile {profile_id!r} has no source urls in "
            f"functions/{'/'.join(SCAN_PROFILES_PATH)} -- this scanner cannot run "
            "until its sources are filled in; it is refused rather than scanned empty"
        )
    return resolved


def _envelope_scan_profile_id(envelope: TaskEnvelope) -> str:
    """The loop task's own `params.profile_id`, or the market-intelligence
    default. Metadata values arrive as strings (worker._task_metadata)."""
    if envelope.metadata:
        return str(envelope.metadata.get("profile_id") or DEFAULT_SCAN_PROFILE_ID)
    return DEFAULT_SCAN_PROFILE_ID

# F-INGEST-EVIDENCE-WINDOW (this change). What the model actually got to
# reason over was the first 2000 characters of each fetched body, RAW --
# and 3 of the market-intelligence profile's 4 URLs are RSS feeds, where those first
# characters are largely <channel> preamble (title, link, ttl, image,
# self-referencing atom:link) rather than a single article. The scan was
# being asked for at least 3 attributed signals across 2 domains from an
# evidence set that was mostly markup.
#
# Two changes, both here rather than in mcp-web: fetch_url stays a generic
# fetch tool with one job, and evidence SHAPING is this handler's concern.
#   1. Feed bodies are reduced to their items (title / summary / date)
#      before the budget is applied, so the budget buys article text.
#   2. Non-feed bodies (learn.microsoft.com's what's-new page) have script,
#      style and tags stripped for the same reason.
#
# Deliberately regex-based, not an XML parser: the input is untrusted
# third-party markup, and ElementTree is vulnerable to entity-expansion
# ("billion laughs") without defusedxml. Matching tags with a bounded
# regex over an already-capped string cannot expand anything, needs no new
# dependency, and this is evidence shaping -- not fidelity parsing, where
# a real parser would be worth the risk.

DEFAULT_INGEST_SOURCE_CHARS = 8000
INGEST_MAX_FEED_ITEMS = 12

# max_tokens for the ingest completion. The gateway client's 1536 default
# is a tight ceiling for up to 8 signals of headline + so_what + URL plus a
# summary paragraph, and a truncated completion fails as invalid JSON (the
# F-WEDNESDAY-DRAFT-TRUNCATION failure mode -- see _complete_and_meter's
# docstring). Raised alongside the input budget so both ends of the call
# have room.
INGEST_MAX_TOKENS = 2048

_FEED_ITEM_RE = re.compile(r"<(item|entry)\b[^>]*>(.*?)</\1>", re.DOTALL | re.IGNORECASE)
_FEED_TITLE_RE = re.compile(r"<title\b[^>]*>(.*?)</title>", re.DOTALL | re.IGNORECASE)
_FEED_SUMMARY_RE = re.compile(
    r"<(description|summary)\b[^>]*>(.*?)</\1>", re.DOTALL | re.IGNORECASE
)
_FEED_DATE_RE = re.compile(
    r"<(pubDate|published|updated)\b[^>]*>(.*?)</\1>", re.DOTALL | re.IGNORECASE
)
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]*>")
_CDATA_RE = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.DOTALL)


def _clean_markup_text(raw: str) -> str:
    """CDATA unwrapped, tags dropped, entities decoded, whitespace collapsed."""
    text = _CDATA_RE.sub(r"\1", raw)
    text = _TAG_RE.sub(" ", text)
    return " ".join(html_module.unescape(text).split())


def _feed_item_lines(body: str, max_items: int = INGEST_MAX_FEED_ITEMS) -> list[str]:
    """One line per feed item, newest-first as the feed itself ordered them.
    Empty list when the body carries no <item>/<entry> elements, which is
    how a caller tells a feed from a page without sniffing content types."""
    lines: list[str] = []
    for match in _FEED_ITEM_RE.finditer(body):
        block = match.group(2)
        title = _clean_markup_text(m.group(1)) if (m := _FEED_TITLE_RE.search(block)) else ""
        summary = _clean_markup_text(m.group(2)) if (m := _FEED_SUMMARY_RE.search(block)) else ""
        date = _clean_markup_text(m.group(2)) if (m := _FEED_DATE_RE.search(block)) else ""
        parts = [part for part in (date, title, summary) if part]
        if not parts:
            continue
        lines.append(" | ".join(parts))
        if len(lines) >= max_items:
            break
    return lines


def _shape_source_evidence(body: str, source_chars: int) -> str:
    """Feed items where the body is a feed, de-marked-up text otherwise,
    truncated to `source_chars`. A body with no markup at all (a plain-text
    response, or a test double's canned string) passes through unchanged
    apart from whitespace collapsing."""
    lines = _feed_item_lines(body)
    shaped = "\n".join(lines) if lines else _clean_markup_text(_SCRIPT_STYLE_RE.sub(" ", body))
    return shaped[:source_chars]


# F-INGEST-NO-MEMORY (this change). Every scan started cold. The
# market-intelligence profile runs DAILY against a THIRTY-day horizon, so
# the same story stayed in-window -- and eligible to be re-reported -- for
# up to thirty consecutive runs. `vault_signal_lookup` was declared in
# function 09's tools.yaml from the start and never implemented anywhere;
# dedupe-signal-cards, the task that would have caught repeats downstream,
# is one of the seventeen no-ops.
#
# The Vault already answers this: GET /signals is in the frozen vault-api
# contract. It takes limit/offset only, no server-side filter, so the
# narrowing to "this profile, inside this horizon" happens here.
#
# The exclusion list is given to the MODEL rather than applied as a hard
# post-filter, on purpose. schema.json requires at least 3 signals; a hard
# filter that dropped repeats could push a batch under that floor and fail
# a scan that had honestly found nothing new -- which would punish the
# system for telling the truth. So the prompt is asked to prefer genuinely
# new items and not to pad, and repeats are COUNTED and surfaced instead
# (ingest_signals_repeats, and repeat_count on the result_ref). A repeat
# rate that stays high is real evidence the horizon or the source list
# needs work, which nothing measured before.

RECENT_SIGNAL_SCAN_LIMIT = 100
RECENT_SIGNAL_HEADLINE_CAP = 40

# Vault signal_type values written by a scan. function 09 emits `signals`,
# the eleven fan-out scanners emit `cards`; both are batches of attributed
# items under a profile topic, so both feed the same cross-run memory.
SIGNAL_BATCH_TYPE = "market_signal_batch"
CARD_BATCH_TYPE = "scanner_card_batch"
# Source-promotion probe results. Not a scan batch -- deliberately
# excluded from SCAN_BATCH_TYPES below so cross-run memory never
# treats a probe as a reported signal.
PROBE_BATCH_TYPE = "source_probe_batch"
SCAN_BATCH_TYPES = frozenset({SIGNAL_BATCH_TYPE, CARD_BATCH_TYPE})


def _batch_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """The attributed items in a scan batch, whichever key its function
    uses -- function 09 says `signals`, the eleven scanners say `cards`."""
    items = payload.get("signals")
    if items is None:
        items = payload.get("cards")
    return items or []


def _parse_iso_timestamp(raw: Any) -> datetime | None:
    """Vault timestamps are RFC 3339; tolerate a trailing Z and treat a
    naive value as UTC. Returns None for anything unparseable rather than
    raising -- a malformed timestamp on one historical row must not sink
    today's scan."""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _already_captured(
    vault: VaultClientExt, sources: dict[str, Any], *, now: datetime | None = None
) -> list[dict[str, str]]:
    """Headline + source_url for every signal this profile already recorded
    inside its own horizon, newest first, capped.

    Matched on the batch's `topic` rather than on a profile id: the signals
    payload is function 09's schema-validated output, whose schema sets
    additionalProperties false, so a profile id cannot be smuggled into it
    -- and topic is unique per profile by construction.

    Never raises. A Vault that is unreachable or slow degrades this scan to
    the cold behaviour it had before this existed, which is worse but not
    broken; failing the scan outright over a missing memory would be a
    worse trade."""
    horizon = timedelta(days=int(sources.get("horizon_days", 30)))
    cutoff = (now or datetime.now(timezone.utc)) - horizon
    captured: list[dict[str, str]] = []
    try:
        rows = vault.list_signals(limit=RECENT_SIGNAL_SCAN_LIMIT)
    except Exception as exc:  # noqa: BLE001 - see docstring: memory is best-effort
        log_event(
            logger,
            logging.WARNING,
            "ingest_signals_memory_unavailable",
            error=sanitize_exception_text(exc),
        )
        return []

    for row in rows:
        payload = row.get("payload") or {}
        if row.get("signal_type") not in SCAN_BATCH_TYPES:
            continue
        if payload.get("topic") != sources["topic"]:
            continue
        received = _parse_iso_timestamp(row.get("received_at") or row.get("created_at"))
        if received is not None and received < cutoff:
            continue
        for signal in _batch_items(payload):
            headline = str(signal.get("headline", "")).strip()
            url = str(signal.get("source_url", "")).strip()
            if headline:
                captured.append({"headline": headline, "source_url": url})
            if len(captured) >= RECENT_SIGNAL_HEADLINE_CAP:
                return captured
    return captured


def _count_repeats(output: dict[str, Any], captured: list[dict[str, str]]) -> int:
    """How many emitted signals restate something already captured, matched
    on source_url first (the same article) and headline second (the same
    story from a re-publication). Measurement only -- nothing is dropped."""
    seen_urls = {item["source_url"] for item in captured if item["source_url"]}
    seen_headlines = {item["headline"].casefold() for item in captured}
    repeats = 0
    for signal in _batch_items(output):
        url = str(signal.get("source_url", "")).strip()
        headline = str(signal.get("headline", "")).strip().casefold()
        if (url and url in seen_urls) or (headline and headline in seen_headlines):
            repeats += 1
    return repeats


def _build_ingest_user_content(
    sources: dict[str, Any],
    fetched: list[dict[str, str]],
    captured: list[dict[str, str]] | None = None,
) -> str:
    lines = [
        f"Topic: {sources['topic']}",
        f"Horizon (days): {sources['horizon_days']}",
    ]
    if captured:
        lines += [
            "",
            "Already captured in this horizon (do not re-report these as new; "
            "prefer genuinely new items, and do not pad to reach the minimum):",
        ]
        lines += [f"- {item['headline']} ({item['source_url']})" for item in captured]
    lines += ["", "Retrieved evidence (fetch_url results, truncated):"]
    for item in fetched:
        lines.append(f"--- SOURCE: {item['url']} ---")
        lines.append(item["body"] or "(empty response body)")
    return "\n".join(lines)

def ingest_signals_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    sources = _resolve_scan_profile(_envelope_scan_profile_id(envelope))
    configured_urls = list(sources["urls"])
    min_sources, min_domains = _ingest_floors(sources)
    source_chars = _ingest_source_chars(sources)

    with build_mcp_web_client() as mcp:
        fetched: list[dict[str, str]] = []
        failed_urls: list[str] = []
        for url in configured_urls:
            try:
                result = mcp.call_tool("fetch_url", {"url": url})
            except Exception as exc:  # noqa: BLE001 - one bad source must not sink the whole scan
                failed_urls.append(url)
                log_event(
                    logger,
                    logging.WARNING,
                    "fetch_url_failed",
                    url=url,
                    error=sanitize_exception_text(exc),
                )
                continue
            fetched.append(
                {
                    "url": url,
                    "body": _shape_source_evidence(str(result.get("body", "")), source_chars),
                }
            )

    if not fetched:
        raise DispatchError(
            f"ingest-signals: every source configured for scan profile "
            f"{sources['profile_id']!r} failed to fetch"
        )

    # Checked BEFORE the model call, so a scan that already cannot meet its
    # contract costs nothing to fail (F-E).
    _assert_ingest_floor(
        "retrieval", [item["url"] for item in fetched], min_sources, min_domains
    )

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
                # Recorded alongside the fetched set so the Vault -- not
                # only a log line somebody has to go looking for -- carries
                # how complete this scan actually was (F-E).
                "source_urls_configured": configured_urls,
                "scan_profile_id": sources["profile_id"],
                "proof_circuit_tag": PROOF_CIRCUIT_TAG if is_proof_circuit(envelope) else None,
            },
        )

        system_prompt = _read_prompt("09-market-intelligence-director")
        captured = _already_captured(vault, sources)

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
                        captured=captured,
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

        used_urls = [item["url"] for item in used_sources]

        # Re-checked AFTER the redaction fallback, which drops sources one
        # at a time and can take a set that passed the retrieval check
        # below the floor (F-E).
        _assert_ingest_floor("the redaction fallback", used_urls, min_sources, min_domains)

        if failed_urls or skipped_sources:
            # One grep-able line stating exactly how complete the day's
            # scan was. Emitted only when something was actually lost, so
            # its presence in the log IS the signal.
            log_event(
                logger,
                logging.WARNING,
                "ingest_signals_degraded",
                configured_count=len(configured_urls),
                used_count=len(used_urls),
                distinct_domain_count=len(_distinct_domains(used_urls)),
                failed_urls=failed_urls,
                redaction_skipped_urls=[item["url"] for item in skipped_sources],
            )

        output = _parse_json_content(response["content"])
        # F-A: schema.json is the contract, so make it the contract at
        # runtime and not only in CI against a mock.
        _validate_function_output(FUNCTION_ID_09, output)
        _assert_signal_domain_floor(output, min_domains, len(_distinct_domains(used_urls)))

        repeat_count = _count_repeats(output, captured)
        if repeat_count:
            log_event(
                logger,
                logging.WARNING,
                "ingest_signals_repeats",
                profile_id=sources["profile_id"],
                repeat_count=repeat_count,
                signal_count=len(_batch_items(output)),
                already_captured_count=len(captured),
            )

        signal = vault.create_signal(
            source="function-09-market-intelligence-director",
            signal_type=SIGNAL_BATCH_TYPE,
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
            # Additive keys (result_ref is untyped JSONB): an operator
            # reading /status sees a degraded-but-passing scan for what it
            # is, instead of an unqualified "completed" (F-E).
            "sources_configured": len(configured_urls),
            "sources_used": len(used_urls),
            "scan_profile_id": sources["profile_id"],
            # How much of today's batch restates something already in the
            # Vault inside this horizon. Measured, not filtered -- see
            # _already_captured's own note on why.
            "repeat_count": repeat_count,
            "already_captured_count": len(captured),
        },
    )
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)

# ---------------------------------------------------------------------
# probe-sources — the source promotion pipeline (F-SOURCE-DISCOVERY)
# ---------------------------------------------------------------------
#
# Eleven scanner profiles ship without urls because nobody has written
# down where to read each sector, and the obvious fix -- let the system
# find its own sources -- runs straight into a circularity: a candidate
# cannot be evaluated without fetching it, and fetching it requires
# allow-listing, which is the decision the evaluation exists to inform.
#
# The pipeline resolves that by splitting the capability in two.
# mcp-web's probe_url reads MCP_WEB_PROBE_ALLOWLIST -- a different list
# from the production egress allow-list fetch_url uses -- and returns
# only SHAPE: status, content type, whether it parses as a feed, item
# count, extractable text size, and up to five item titles. Never the
# body. Probing is therefore a strictly smaller capability than scanning,
# so granting it is a smaller decision.
#
# Scoring is deterministic, for the same reason score-signals is: a
# source is judged on measurable properties, and a model asked "is this a
# good source?" would be inventing an opinion where arithmetic will do.
#
# PROMOTION IS NEVER AUTOMATIC. The handler ends by raising a real
# gate-check against config.source_promotion (autonomy level 1, one human
# approver) carrying the full probe detail and the reasoning as
# evidence_summary. Nothing here edits scan-profiles.yaml or the Bicep;
# a person reads the card and makes that edit. The egress allow-list is
# AC-17's security control, and a pipeline that could widen it unattended
# would be a pipeline that lets discovered content decide what the system
# may reach.

FUNCTION_ID_SOURCE_PROMOTION = "config.source_promotion"
SOURCE_PROMOTION_ACTION_CLASS = "configure"
SOURCE_CANDIDATES_PATH = ("_shared", "source-candidates.yaml")

# Score thresholds. A candidate at or above PROMOTE_SCORE is recommended;
# below REJECT_SCORE it is recommended against. Between them the card says
# "needs a human eye" rather than pretending the arithmetic decided.
PROMOTE_SCORE = 0.6
REJECT_SCORE = 0.3

# Minimum extractable text for a source to be worth a scan's evidence
# budget at all -- below this the daily scan would spend a fetch on
# nothing. Calibrated against the market-intelligence profile's own live
# feeds, which clear it comfortably.
MIN_USEFUL_EXTRACTABLE_CHARS = 500


def _load_source_candidates() -> list[dict[str, Any]]:
    path = functions_dir().joinpath(*SOURCE_CANDIDATES_PATH)
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    return list(document.get("candidates") or [])


def _score_probe(probe: dict[str, Any]) -> tuple[float, list[str]]:
    """Score a probe result 0..1, with the reasons that produced it.

    The reasons are the point: they go on the approval card verbatim, so a
    reviewer sees WHY a number came out the way it did rather than being
    asked to trust it. Every component is something the probe measured."""
    reasons: list[str] = []
    status = int(probe.get("status_code") or 0)
    if status != 200:
        return 0.0, [f"returned HTTP {status or 'no response'} — not reachable"]

    score = 0.4
    reasons.append("reachable (HTTP 200)")

    if probe.get("is_feed"):
        score += 0.2
        item_count = int(probe.get("item_count") or 0)
        reasons.append(f"parses as a feed with {item_count} item(s)")
        if item_count >= 5:
            score += 0.1
            reasons.append("carries enough items for a daily scan to find movement")
        else:
            reasons.append("few items — may go quiet between scans")
    else:
        reasons.append("not a feed — a page scan re-reads the same content until it changes")

    extractable = int(probe.get("extractable_chars") or 0)
    if extractable >= MIN_USEFUL_EXTRACTABLE_CHARS:
        score += 0.2
        reasons.append(f"{extractable} characters of extractable text")
    else:
        reasons.append(
            f"only {extractable} characters of extractable text — "
            f"below the {MIN_USEFUL_EXTRACTABLE_CHARS} a scan needs to attribute anything"
        )

    if probe.get("sample_titles"):
        score += 0.1
        reasons.append(f"sample titles retrieved for review ({len(probe['sample_titles'])})")
    else:
        reasons.append("no item titles found — a reviewer cannot see what this source carries")

    return round(min(score, 1.0), 2), reasons


def _promotion_verdict(score: float) -> str:
    if score >= PROMOTE_SCORE:
        return "recommend_promote"
    if score < REJECT_SCORE:
        return "recommend_reject"
    return "needs_review"


def _render_promotion_evidence(results: list[dict[str, Any]]) -> str:
    """The detail list and reasoning that goes on the approval card.

    Written for a person deciding whether to widen an egress allow-list,
    so it leads with what they are being asked to allow, states the
    machine's recommendation and why, and shows sample titles as evidence
    of what the source actually carries."""
    lines = [
        f"{len(results)} candidate source(s) probed in the sandbox "
        "(metadata only — no content was fetched into any scan).",
        "",
        "Approving this card authorises adding the recommended hosts to "
        "mcp-web's production egress allow-list and to their scan profile.",
        "",
    ]
    for result in results:
        probe = result["probe"]
        lines.append(f"— {result['candidate_id']} → profile {result['profile_id']}")
        lines.append(f"  url: {result['url']}")
        lines.append(f"  host: {result['host']}")
        lines.append(f"  score: {result['score']} → {result['verdict'].replace('_', ' ')}")
        lines.append(f"  why: {'; '.join(result['reasons'])}")
        if result.get("rationale"):
            lines.append(f"  proposed because: {result['rationale']}")
        titles = probe.get("sample_titles") or []
        if titles:
            lines.append("  sample titles:")
            lines += [f"    · {title}" for title in titles[:5]]
        lines.append("")
    return "\n".join(lines).strip()


# ---------------------------------------------------------------------
# propose-sources — function 17, the other half of source discovery
# ---------------------------------------------------------------------
#
# The probe pipeline could test candidates but only a human could think of
# them, so eleven profiles stayed empty. Function 17 proposes addresses
# from its own knowledge for any profile that has none.
#
# IT IS PERMITTED NOTHING (see its tools.yaml). No fetch, no search, no
# Vault read: giving a proposer retrieval would let a model's own
# suggestion cause an outbound request to an arbitrary host, which is the
# circularity the sandboxed probe exists to break. It reasons from the
# profile's topic and watchlist prose and returns a hypothesis.
#
# AND A PROPOSAL CANNOT REACH THE PROBE ON ITS OWN. probe_url reads
# MCP_WEB_PROBE_ALLOWLIST, so a host nobody has cleared is unprobeable --
# by design, and it means machine-proposed hosts need a human decision
# BEFORE they can even be measured. That is a second, smaller gate in
# front of the promotion gate, and the smaller one is the more important:
# it is the one that stops a model's guess from causing a network call.
#
# So this handler ends where every other risky path here ends: a
# gate-check card, carrying each proposed host, who publishes it, why it
# was proposed and how sure the model is that the address even exists.
# Approving it authorises adding those hosts to the PROBE allow-list only
# -- never the scan one, which still requires the probe evidence and the
# second card.

FUNCTION_ID_17 = "17-source-scout"
PROPOSAL_BATCH_TYPE = "source_proposal_batch"


def _profiles_needing_sources() -> list[dict[str, Any]]:
    """Every profile with no urls, defaults merged. These are exactly the
    scanners completing as not_configured every morning."""
    document = _load_scan_profiles()
    defaults = document.get("defaults", {})
    return [
        {**defaults, **profile}
        for profile in document.get("profiles", [])
        if not profile.get("urls")
    ]


def _known_candidate_urls(profile_id: str) -> list[str]:
    """What the register already holds for this profile, so the scout is
    told not to re-propose it."""
    return [
        str(candidate.get("url", ""))
        for candidate in _load_source_candidates()
        if candidate.get("profile_id") == profile_id
    ]


def _render_proposal_evidence(proposals: list[dict[str, Any]]) -> str:
    """The detail list for the probe-allow-list card.

    A reviewer is being asked to let an automated step make outbound
    requests to these hosts, so the card leads with that, and shows the
    model's own confidence that each address exists at all -- which is the
    honest measure of how much of this list is likely to be wrong."""
    hosts = sorted({item["host"] for item in proposals if item["host"]})
    lines = [
        f"{len(proposals)} candidate source(s) proposed by function 17 across "
        f"{len({item['profile_id'] for item in proposals})} unsourced profile(s).",
        "",
        "Nothing has been fetched. Function 17 has no retrieval tools at all — "
        "these are addresses it believes exist, not addresses anyone has tested.",
        "",
        "Approving this card authorises adding these hosts to mcp-web's PROBE "
        "allow-list, so the weekly probe may fetch their metadata (status, feed "
        "shape, item count, sample titles — never the body). It does NOT put them "
        "on the scan allow-list: that needs the probe evidence and a second card.",
        "",
        f"Hosts: {', '.join(hosts) if hosts else '(none)'}",
        "",
    ]
    for item in proposals:
        lines.append(f"— {item['url']}")
        lines.append(f"  profile: {item['profile_id']}  ·  publisher: {item['publisher']}")
        lines.append(f"  kind: {item['source_kind']}  ·  address confidence: {item['confidence']}")
        lines.append(f"  why: {item['rationale']}")
        lines.append("")
    return "\n".join(lines).strip()


def propose_sources_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    profiles = _profiles_needing_sources()
    if not profiles:
        log_event(logger, logging.INFO, "no_profiles_need_sources")
        db.set_result_ref(task_id, {"status": "nothing_to_propose", "proposal_count": 0})
        db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
        db.advance_dependents(task_id)
        return

    proposals: list[dict[str, Any]] = []
    with build_vault_client() as vault:
        campaign_id = vault.get_or_create_campaign(
            _campaign_name(envelope), function_id=FUNCTION_ID_17
        )
        agent_run = vault.create_agent_run(
            agent_name=_agent_name("source-scout", envelope),
            campaign_id=campaign_id,
            function_id=FUNCTION_ID_17,
            status="running",
            input_payload={"profile_count": len(profiles)},
        )
        system_prompt = _read_prompt(FUNCTION_ID_17)

        with emit_task_span(
            "propose-sources",
            function_id=FUNCTION_ID_17,
            task_ref=task_id,
            model="claude-sonnet",
            run_id=str(envelope.campaign_id),
        ) as span:
            total_cost = 0.0
            with build_gateway_client() as gateway:
                for profile in profiles:
                    payload = {
                        "profile_id": profile["profile_id"],
                        "topic": profile["topic"],
                        "watchlist_note": profile.get("watchlist_note", ""),
                        "existing_urls": list(profile.get("urls") or []),
                        "existing_candidates": _known_candidate_urls(profile["profile_id"]),
                    }
                    _validate_function_input(FUNCTION_ID_17, payload)
                    response, cost = _complete_and_meter(
                        gateway,
                        vault,
                        model="claude-sonnet",
                        system_prompt=system_prompt,
                        user_content=json.dumps(payload),
                        agent_run_id=agent_run["id"],
                    )
                    total_cost += cost
                    output = _parse_json_content(response["content"])
                    _validate_function_output(FUNCTION_ID_17, output)
                    for candidate in output.get("candidates", []):
                        url = str(candidate.get("url", ""))
                        proposals.append(
                            {
                                "profile_id": profile["profile_id"],
                                "url": url,
                                "host": (urlparse(url).hostname or "").lower(),
                                "publisher": str(candidate.get("publisher", "")),
                                "source_kind": str(candidate.get("source_kind", "")),
                                "rationale": str(candidate.get("rationale", "")),
                                "confidence": str(candidate.get("confidence", "")),
                            }
                        )
            set_span_attribute(span, "cost", total_cost)

        proposal_batch = vault.create_signal(
            source="source-scout-pipeline",
            signal_type=PROPOSAL_BATCH_TYPE,
            payload={"proposals": proposals},
            campaign_id=campaign_id,
            function_id=FUNCTION_ID_17,
        )
        vault.update_agent_run(
            agent_run["id"],
            status="succeeded",
            output_payload={"proposal_count": len(proposals)},
            completed_at=_now_iso(),
        )

    evidence = _render_proposal_evidence(proposals)
    content_hash = hashlib.sha256(
        json.dumps(
            sorted({item["host"] for item in proposals if item["host"]}),
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    with build_gatekeeper_client() as gatekeeper:
        decision = gatekeeper.gate_check(
            agent_run_id=str(agent_run["id"]),
            function_id=FUNCTION_ID_SOURCE_PROMOTION,
            action_class=SOURCE_PROMOTION_ACTION_CLASS,
            content_hash=content_hash,
            preview_title=(
                f"Probe allow-list — {len({item['host'] for item in proposals})} host(s) "
                f"proposed for {len(profiles)} unsourced profile(s)"
            ),
            preview_reference=f"proposal-batch://{proposal_batch['id']}",
            evidence_summary=evidence,
        )

    db.set_result_ref(
        task_id,
        {
            "status": "proposed",
            "proposal_batch_id": proposal_batch["id"],
            "agent_run_id": agent_run["id"],
            "campaign_id": campaign_id,
            "profile_count": len(profiles),
            "proposal_count": len(proposals),
            "proposed_hosts": sorted({item["host"] for item in proposals if item["host"]}),
            "decision_id": decision.get("decision_id"),
            "outcome": decision.get("outcome"),
            "content_hash": content_hash,
        },
    )
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)


def probe_sources_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    candidates = _load_source_candidates()
    if not candidates:
        raise DispatchError(
            f"probe-sources: functions/{'/'.join(SOURCE_CANDIDATES_PATH)} lists no candidates"
        )

    results: list[dict[str, Any]] = []
    with build_mcp_web_client() as mcp:
        for candidate in candidates:
            url = str(candidate.get("url", ""))
            try:
                probe = mcp.call_tool("probe_url", {"url": url})
            except Exception as exc:  # noqa: BLE001 - one unreachable candidate is a RESULT
                log_event(
                    logger,
                    logging.WARNING,
                    "probe_url_failed",
                    url=url,
                    error=sanitize_exception_text(exc),
                )
                probe = {"status_code": 0, "error": "probe failed"}
            score, reasons = _score_probe(probe)
            results.append(
                {
                    "candidate_id": candidate.get("candidate_id"),
                    "profile_id": candidate.get("profile_id"),
                    "url": url,
                    "host": (urlparse(url).hostname or "").lower(),
                    "rationale": candidate.get("rationale"),
                    "score": score,
                    "verdict": _promotion_verdict(score),
                    "reasons": reasons,
                    "probe": probe,
                }
            )

    results.sort(key=lambda item: -item["score"])
    recommended = [item for item in results if item["verdict"] == "recommend_promote"]

    with build_vault_client() as vault:
        campaign_id = vault.get_or_create_campaign(
            _campaign_name(envelope), function_id=FUNCTION_ID_SOURCE_PROMOTION
        )
        agent_run = vault.create_agent_run(
            agent_name=_agent_name("source-promotion-scout", envelope),
            campaign_id=campaign_id,
            function_id=FUNCTION_ID_SOURCE_PROMOTION,
            status="running",
            input_payload={"candidate_count": len(candidates)},
        )
        probe_batch = vault.create_signal(
            source="source-promotion-pipeline",
            signal_type=PROBE_BATCH_TYPE,
            payload={"results": results},
            campaign_id=campaign_id,
            function_id=FUNCTION_ID_SOURCE_PROMOTION,
        )
        vault.update_agent_run(
            agent_run["id"],
            status="succeeded",
            output_payload={
                "probed": len(results),
                "recommended": [item["candidate_id"] for item in recommended],
            },
            completed_at=_now_iso(),
        )

    evidence = _render_promotion_evidence(results)
    content_hash = hashlib.sha256(
        json.dumps(
            [
                {"candidate_id": item["candidate_id"], "host": item["host"], "score": item["score"]}
                for item in results
            ],
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    with build_gatekeeper_client() as gatekeeper:
        with emit_task_span(
            "probe-sources",
            function_id=FUNCTION_ID_SOURCE_PROMOTION,
            task_ref=task_id,
            model="none",
            cost=0.0,
            run_id=str(envelope.campaign_id),
        ):
            decision = gatekeeper.gate_check(
                agent_run_id=str(agent_run["id"]),
                function_id=FUNCTION_ID_SOURCE_PROMOTION,
                action_class=SOURCE_PROMOTION_ACTION_CLASS,
                content_hash=content_hash,
                preview_title=(
                    f"Source promotion — {len(recommended)} of {len(results)} candidate(s) "
                    "recommended for the scan allow-list"
                ),
                preview_reference=f"probe-batch://{probe_batch['id']}",
                evidence_summary=evidence,
            )

    db.set_result_ref(
        task_id,
        {
            "probe_batch_id": probe_batch["id"],
            "agent_run_id": agent_run["id"],
            "campaign_id": campaign_id,
            "probed_count": len(results),
            "recommended_candidate_ids": [item["candidate_id"] for item in recommended],
            "decision_id": decision.get("decision_id"),
            "outcome": decision.get("outcome"),
            "approval_id": decision.get("approval_id"),
            "content_hash": content_hash,
        },
    )
    # Completes as soon as /gate-check responds -- the human decision
    # arrives asynchronously on the approval surface, exactly as
    # request-approval does. Nothing downstream edits config either way:
    # promotion is a person editing scan-profiles.yaml and main.bicep.
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)


# ---------------------------------------------------------------------
# S10 intelligence fan-out — the eleven scanners (F1)
# ---------------------------------------------------------------------
#
# The architecture review's highest-value finding: eleven complete
# function packages -- prompt, schema, tools, skill, 5 evals each -- with
# a task in daily-signal-loop.yaml and NO DISPATCH_TABLE entry, so every
# one of them fell through to legacy_task_pass_through. The loop reported
# 23 completed tasks every morning while ~74% of its declared work
# produced nothing.
#
# ONE handler, eleven registrations. The eleven share an identical output
# contract -- {topic, horizon_days, [vertical], summary, cards[]} with
# card_type / taxonomy / evidence_grade / confidence per card -- so this
# is one function parameterised by (function_id, profile_id), not eleven
# near-copies of the kind C1 already flags this module for.
#
# UNSOURCED PROFILES COMPLETE, THEY DO NOT FAIL. All eleven profiles ship
# without urls today (see functions/_shared/scan-profiles.yaml's header:
# nobody has written down where to read each sector yet). Failing them
# would put eleven FAILED tasks on the board every morning and cascade
# into dedupe and both rollups -- making red the normal state, which is
# how a red loop stops meaning anything. Instead an unsourced scanner
# completes immediately with status="not_configured" on its result_ref
# and a warning naming the profile: no model call, no cost, and the
# emptiness is queryable rather than invisible, which is the actual
# difference from the no-op it replaces. Filling in that profile's urls
# is all it takes to make the scanner live -- no code change.
#
# Cards are persisted as a Vault signal batch, NOT as opportunity_cards
# rows. dedupe-signal-cards is still a no-op, and eleven scanners running
# the same three shared listening scopes will legitimately surface one
# event several times -- writing 11 batches straight to opportunity_cards
# would put that duplication in the table the morning brief reads. Card
# rows are dedupe's job when dedupe exists.

SCANNER_TASKS: dict[str, tuple[str, str, str]] = {
    # task_type: (function_id, default profile_id, agent_name)
    "competitor-discovery-scan": (
        "10-competitor-discovery-scanner",
        "competitor-discovery",
        "competitor-discovery-scanner",
    ),
    "competitor-change-monitor": (
        "11-competitor-change-monitor",
        "competitor-change",
        "competitor-change-monitor",
    ),
    "competitive-positioning-analysis": (
        "12-competitive-positioning-analyst",
        "competitive-positioning",
        "competitive-positioning-analyst",
    ),
    "competitor-content-performance-scout": (
        "13-competitor-content-performance-scout",
        "competitor-content-performance",
        "competitor-content-performance-scout",
    ),
    "fabric-ecosystem-scout": (
        "16-microsoft-fabric-ecosystem-scout",
        "fabric-ecosystem",
        "fabric-ecosystem-scout",
    ),
    "vertical-scan-logistics-fleet": (
        "18-01-vertical-intel-logistics-fleet",
        "vertical-logistics-fleet",
        "vertical-intel-logistics-fleet",
    ),
    "vertical-scan-mining-industrial": (
        "18-02-vertical-intel-mining-industrial",
        "vertical-mining-industrial",
        "vertical-intel-mining-industrial",
    ),
    "vertical-scan-manufacturing": (
        "18-03-vertical-intel-manufacturing",
        "vertical-manufacturing",
        "vertical-intel-manufacturing",
    ),
    "vertical-scan-construction": (
        "18-04-vertical-intel-construction",
        "vertical-construction",
        "vertical-intel-construction",
    ),
    "vertical-scan-fmcg-beverage": (
        "18-05-vertical-intel-fmcg-beverage",
        "vertical-fmcg-beverage",
        "vertical-intel-fmcg-beverage",
    ),
    "vertical-scan-financial-services": (
        "18-06-vertical-intel-financial-services",
        "vertical-financial-services",
        "vertical-intel-financial-services",
    ),
}


def _complete_unconfigured_scan(
    task_id: str, db: Any, *, task_type: str, function_id: str, profile_id: str
) -> None:
    log_event(
        logger,
        logging.WARNING,
        "scan_profile_not_configured",
        task_type=task_type,
        function_id=function_id,
        profile_id=profile_id,
    )
    db.set_result_ref(
        task_id,
        {
            "status": "not_configured",
            "profile_id": profile_id,
            "function_id": function_id,
            "reason": (
                f"scan profile {profile_id!r} has no source urls in "
                f"functions/{'/'.join(SCAN_PROFILES_PATH)}"
            ),
        },
    )
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)


def _make_scanner_handler(task_type: str, function_id: str, profile_id: str, agent_name: str):
    """Build one of the eleven fan-out handlers. Structurally the same scan
    ingest_signals_handler runs -- fetch, floors, shaped evidence, redaction
    fallback, schema validation, cross-run memory -- against a different
    package's prompt and a different profile."""

    def handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
        resolved_profile_id = (
            str(envelope.metadata.get("profile_id")) if envelope.metadata else None
        ) or profile_id
        sources = _resolve_scan_profile(resolved_profile_id, require_urls=False)
        if not sources.get("urls"):
            _complete_unconfigured_scan(
                task_id,
                db,
                task_type=task_type,
                function_id=function_id,
                profile_id=resolved_profile_id,
            )
            return

        configured_urls = list(sources["urls"])
        min_sources, min_domains = _ingest_floors(sources)
        source_chars = _ingest_source_chars(sources)

        with build_mcp_web_client() as mcp:
            fetched: list[dict[str, str]] = []
            failed_urls: list[str] = []
            for url in configured_urls:
                try:
                    result = mcp.call_tool("fetch_url", {"url": url})
                except Exception as exc:  # noqa: BLE001 - one bad source must not sink the scan
                    failed_urls.append(url)
                    log_event(
                        logger,
                        logging.WARNING,
                        "fetch_url_failed",
                        url=url,
                        error=sanitize_exception_text(exc),
                    )
                    continue
                fetched.append(
                    {
                        "url": url,
                        "body": _shape_source_evidence(str(result.get("body", "")), source_chars),
                    }
                )

        if not fetched:
            raise DispatchError(
                f"{task_type}: every source configured for scan profile "
                f"{resolved_profile_id!r} failed to fetch"
            )
        _assert_ingest_floor(
            "retrieval", [item["url"] for item in fetched], min_sources, min_domains
        )

        with build_vault_client() as vault:
            campaign_id = vault.get_or_create_campaign(
                _campaign_name(envelope), function_id=function_id
            )
            agent_run = vault.create_agent_run(
                agent_name=_agent_name(agent_name, envelope),
                campaign_id=campaign_id,
                function_id=function_id,
                status="running",
                input_payload={
                    "topic": sources["topic"],
                    "horizon_days": sources["horizon_days"],
                    "source_urls": [item["url"] for item in fetched],
                    "source_urls_configured": configured_urls,
                    "scan_profile_id": resolved_profile_id,
                    "proof_circuit_tag": PROOF_CIRCUIT_TAG if is_proof_circuit(envelope) else None,
                },
            )

            system_prompt = _read_prompt(function_id)
            captured = _already_captured(vault, sources)

            with emit_task_span(
                task_type,
                function_id=function_id,
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
                            captured=captured,
                        )
                    )
                set_span_attribute(span, "cost", cost)

            used_urls = [item["url"] for item in used_sources]
            _assert_ingest_floor("the redaction fallback", used_urls, min_sources, min_domains)

            if failed_urls or skipped_sources:
                log_event(
                    logger,
                    logging.WARNING,
                    "ingest_signals_degraded",
                    task_type=task_type,
                    profile_id=resolved_profile_id,
                    configured_count=len(configured_urls),
                    used_count=len(used_urls),
                    distinct_domain_count=len(_distinct_domains(used_urls)),
                    failed_urls=failed_urls,
                    redaction_skipped_urls=[item["url"] for item in skipped_sources],
                )

            output = _parse_json_content(response["content"])
            _validate_function_output(function_id, output)
            _assert_signal_domain_floor(output, min_domains, len(_distinct_domains(used_urls)))

            repeat_count = _count_repeats(output, captured)
            if repeat_count:
                log_event(
                    logger,
                    logging.WARNING,
                    "ingest_signals_repeats",
                    task_type=task_type,
                    profile_id=resolved_profile_id,
                    repeat_count=repeat_count,
                    signal_count=len(_batch_items(output)),
                    already_captured_count=len(captured),
                )

            signal = vault.create_signal(
                source=f"function-{function_id}",
                signal_type=CARD_BATCH_TYPE,
                payload=output,
                campaign_id=campaign_id,
                function_id=function_id,
            )
            vault.update_agent_run(
                agent_run["id"], status="succeeded", output_payload=output, completed_at=_now_iso()
            )

        db.set_result_ref(
            task_id,
            {
                "status": "scanned",
                "vault_signal_id": signal["id"],
                "agent_run_id": agent_run["id"],
                "campaign_id": campaign_id,
                "topic": sources["topic"],
                "profile_id": resolved_profile_id,
                "function_id": function_id,
                "card_count": len(_batch_items(output)),
                "sources_configured": len(configured_urls),
                "sources_used": len(used_urls),
                "repeat_count": repeat_count,
            },
        )
        db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
        db.advance_dependents(task_id)

    handler.__name__ = f"{task_type.replace('-', '_')}_handler"
    return handler


SCANNER_HANDLERS = {
    task_type: _make_scanner_handler(task_type, function_id, profile_id, agent_name)
    for task_type, (function_id, profile_id, agent_name) in SCANNER_TASKS.items()
}


# =======================================================================
# THE SCANNERS' DEAD TAIL
# =======================================================================
#
# All eleven fan-out scanners fed `dedupe-signal-cards`, and every task
# from there down was unregistered -- falling through to
# legacy_task_pass_through, which sets no result_ref and completes the
# task "successfully" having done nothing:
#
#   11 scanners -> dedupe -> strategize -> morning-brief-rollup
#                                       -> executive-brief-rollup
#
# So the scanners ran every morning, cost a model call each, wrote a card
# batch into the Vault -- and nothing read any of it. The morning brief a
# person actually receives is built by draft-brief on the separate
# ingest -> score -> draft path, which never sees a single scanner card.
# Eleven scanners' worth of competitive intelligence went into the Vault
# and stopped.
#
# The rollups render deterministically. Ranking, deduplicating and
# formatting cards that other functions already wrote is arithmetic and
# string work; a model asked to "roll up" would only paraphrase, and
# every paraphrase is a chance to alter a claim that has already been
# through its own function's contract.

DEDUPE_BATCH_TYPE = "deduped_card_batch"
RESPONSE_PLAN_TYPE = "competitive_response_plan"
FUNCTION_ID_25 = "25-competitive-response-strategist"

# Function 25's input caps `cards` at 20 items.
STRATEGIST_CARD_CAP = 20

# Its input schema is additionalProperties:false and does NOT include
# `confidence`, which every scanner card carries -- so a card cannot be
# forwarded verbatim. These are exactly the keys function 25 accepts.
STRATEGIST_CARD_KEYS = (
    "headline",
    "so_what",
    "source_url",
    "card_type",
    "taxonomy",
    "evidence_grade",
)


def _card_identity(card: dict[str, Any]) -> tuple[str, str]:
    """The two things that make two cards the same story.

    A source_url match is the strong signal -- two scanners reaching the
    same article -- and a normalised headline catches the same story
    reported by two publications. Both are compared case- and
    whitespace-insensitively; neither alone is enough, which is why the
    caller checks each independently rather than combining them into one
    key."""
    url = str(card.get("source_url", "")).strip().lower().rstrip("/")
    headline = " ".join(str(card.get("headline", "")).lower().split())
    return url, headline


def _dedupe_cards(batches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One card per story, carrying how many scanners found it.

    `seen_by` is the point of doing this across eleven scanners rather
    than one: a story three separate profiles surfaced independently is a
    stronger signal than a story one did, and that fact exists nowhere
    until the batches are merged. Order is preserved -- first scanner to
    report a story keeps its position -- so the result is stable across
    runs given the same inputs.
    """
    merged: list[dict[str, Any]] = []
    urls: dict[str, int] = {}
    headlines: dict[str, int] = {}

    for batch in batches:
        for card in _batch_items(batch["payload"]):
            url, headline = _card_identity(card)
            index = urls.get(url) if url else None
            if index is None and headline:
                index = headlines.get(headline)
            if index is not None:
                existing = merged[index]
                existing["seen_by"] += 1
                if batch["profile_id"] not in existing["profiles"]:
                    existing["profiles"].append(batch["profile_id"])
                continue
            entry = dict(card)
            entry["seen_by"] = 1
            entry["profiles"] = [batch["profile_id"]]
            merged.append(entry)
            if url:
                urls[url] = len(merged) - 1
            if headline:
                headlines[headline] = len(merged) - 1
    return merged


def _rank_cards(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Most corroborated first, then best-evidenced, then most confident.

    Uses the same CONFIDENCE_SCORES score-signals uses, so "what matters"
    means one thing across the daily loop rather than two that can
    disagree."""
    grades = {"strong": 1.0, "moderate": 0.6, "light": 0.3}
    return sorted(
        cards,
        key=lambda card: (
            card.get("seen_by", 1),
            grades.get(str(card.get("evidence_grade", "")).lower(), 0.0),
            CONFIDENCE_SCORES.get(str(card.get("confidence", "")).lower(), 0.0),
        ),
        reverse=True,
    )


def _collect_scanner_batches(task_id: str, db: Any, vault: Any) -> list[dict[str, Any]]:
    """Every scanner card batch this task depends on.

    Reads this task's own depends_on rows rather than walking lineage:
    resolve_lineage_result stops at the FIRST ancestor carrying a
    result_ref, and this task has eleven of them -- taking one would
    silently discard ten scans. Same reasoning as
    _select_repurpose_source's own note.

    A scanner that completed as not_configured (no source urls on its
    profile) carries no vault_signal_id and is skipped, not failed: an
    unsourced profile is a known, recorded gap, not a reason to lose the
    ten scans that did run."""
    current = db.get_task(task_id) or {}
    batches: list[dict[str, Any]] = []
    for row in db.get_tasks(current.get("depends_on") or []):
        ref = row.get("result_ref") or {}
        signal_id = ref.get("vault_signal_id")
        if not signal_id:
            continue
        try:
            signal = vault.get_signal(signal_id)
        except Exception as exc:  # noqa: BLE001 - one unreadable batch must not sink the merge
            log_event(
                logger,
                logging.WARNING,
                "scanner_batch_unreadable",
                task_id=row.get("task_id"),
                signal_id=signal_id,
                error=sanitize_exception_text(exc),
            )
            continue
        batches.append(
            {
                "profile_id": ref.get("profile_id") or row.get("task_type"),
                "payload": signal.get("payload") or {},
            }
        )
    return batches


def dedupe_signal_cards_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    """Merges eleven scanners' card batches into one ranked, deduplicated
    set. Deterministic -- no model call."""
    with build_vault_client() as vault:
        batches = _collect_scanner_batches(task_id, db, vault)
        raw_count = sum(len(_batch_items(batch["payload"])) for batch in batches)
        cards = _rank_cards(_dedupe_cards(batches))

        campaign_id = vault.get_or_create_campaign(
            _campaign_name(envelope), function_id=FUNCTION_ID_SIGNAL_SCORE
        )
        signal = vault.create_signal(
            source="dedupe-signal-cards",
            signal_type=DEDUPE_BATCH_TYPE,
            payload={"cards": cards},
            campaign_id=campaign_id,
            function_id=FUNCTION_ID_SIGNAL_SCORE,
        )

    log_event(
        logger,
        logging.INFO,
        "signal_cards_deduped",
        scanners_read=len(batches),
        cards_in=raw_count,
        cards_out=len(cards),
        duplicates_removed=raw_count - len(cards),
    )
    db.set_result_ref(
        task_id,
        {
            "vault_signal_id": signal["id"],
            "campaign_id": campaign_id,
            "scanners_read": len(batches),
            "cards_in": raw_count,
            "cards_out": len(cards),
            "cards": cards,
        },
    )
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)


def _strategist_cards(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduped cards reshaped to function 25's exact input contract.

    `confidence` and this handler's own `seen_by`/`profiles` are dropped:
    the schema is additionalProperties:false and lists neither, so a card
    forwarded verbatim is rejected. Cards missing a field the schema
    requires are dropped rather than patched -- inventing a card_type to
    satisfy a validator is how an unchecked claim gets into a plan."""
    shaped = []
    for card in cards[:STRATEGIST_CARD_CAP]:
        entry = {key: card[key] for key in STRATEGIST_CARD_KEYS if card.get(key)}
        if all(entry.get(key) for key in ("headline", "so_what", "source_url", "card_type")):
            shaped.append(entry)
    return shaped


def competitive_response_strategize_handler(
    task_id: str, envelope: TaskEnvelope, db: Any
) -> None:
    """Function 25 over the deduped cards: a severity-ranked response plan."""
    lineage = resolve_lineage_result(task_id, db)
    if lineage is None:
        raise DispatchError("competitive-response-strategize: no deduped-card ancestor")
    _ancestor_task, ancestor_ref = lineage
    cards = _strategist_cards(ancestor_ref.get("cards") or [])
    if not cards:
        # A morning where eleven scanners found nothing citable is a real
        # outcome, and a strategist asked to rank an empty list would be
        # asked to invent one. The rollup reads this and says so.
        log_event(logger, logging.WARNING, "competitive_response_no_cards", task_id=task_id)
        db.set_result_ref(
            task_id, {"status": "no_cards", "campaign_id": ancestor_ref.get("campaign_id")}
        )
        db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
        db.advance_dependents(task_id)
        return

    payload = {"cards": cards}
    _validate_function_input(FUNCTION_ID_25, payload)

    with build_vault_client() as vault:
        campaign_id = vault.get_or_create_campaign(
            _campaign_name(envelope), function_id=FUNCTION_ID_25
        )
        agent_run = vault.create_agent_run(
            agent_name=_agent_name("competitive-response-strategist", envelope),
            campaign_id=campaign_id,
            function_id=FUNCTION_ID_25,
            status="running",
            input_payload={"card_count": len(cards)},
        )
        system_prompt = _read_prompt(FUNCTION_ID_25)
        with emit_task_span(
            "competitive-response-strategize",
            function_id=FUNCTION_ID_25,
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
                    user_content=json.dumps(payload),
                    agent_run_id=agent_run["id"],
                    content_class="public_source_content",
                    max_tokens=3072,
                )
            set_span_attribute(span, "cost", cost)

        output = _parse_json_content(response["content"])
        _validate_function_output(FUNCTION_ID_25, output)
        signal = vault.create_signal(
            source="competitive-response-strategize",
            signal_type=RESPONSE_PLAN_TYPE,
            payload=output,
            campaign_id=campaign_id,
            function_id=FUNCTION_ID_25,
        )
        vault.update_agent_run(
            agent_run["id"], status="succeeded", output_payload=output, completed_at=_now_iso()
        )

    db.set_result_ref(
        task_id,
        {
            "status": "planned",
            "vault_signal_id": signal["id"],
            "campaign_id": campaign_id,
            "agent_run_id": agent_run["id"],
            "summary": output.get("summary", ""),
            "response_plan": output.get("response_plan", []),
        },
    )
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)


def _collect_rollup_inputs(task_id: str, db: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """The deduped cards and the response plan, read from this task's own
    two depends_on rows.

    morning-brief-rollup depends on BOTH dedupe-signal-cards and
    competitive-response-strategize, so lineage resolution would take
    whichever it reached first and lose the other."""
    current = db.get_task(task_id) or {}
    cards_ref: dict[str, Any] = {}
    plan_ref: dict[str, Any] = {}
    for row in db.get_tasks(current.get("depends_on") or []):
        ref = row.get("result_ref") or {}
        if row.get("task_type") == "dedupe-signal-cards":
            cards_ref = ref
        elif row.get("task_type") == "competitive-response-strategize":
            plan_ref = ref
    return cards_ref, plan_ref


def _render_intel_brief(
    cards_ref: dict[str, Any], plan_ref: dict[str, Any]
) -> tuple[str, str]:
    """The competitive-intelligence brief and its executive edition.

    Deterministic, and explicit about provenance: `seen by N scanners` is
    the one fact that only exists because eleven profiles were merged, so
    it leads each line. An empty morning says so plainly rather than
    rendering an empty section that reads like a formatting fault."""
    cards = cards_ref.get("cards") or []
    plan = plan_ref.get("response_plan") or []
    scanners = cards_ref.get("scanners_read", 0)
    removed = max(0, cards_ref.get("cards_in", 0) - cards_ref.get("cards_out", 0))

    full = [
        "# Morning Brief — competitive intelligence",
        "",
        f"{len(cards)} distinct item(s) from {scanners} scanner(s); "
        f"{removed} duplicate(s) merged.",
    ]
    if plan_ref.get("summary"):
        full += ["", plan_ref["summary"]]

    full += ["", "## What the scanners found", ""]
    if cards:
        for card in cards:
            domain = urlparse(str(card.get("source_url", ""))).hostname or "unknown-source"
            seen = card.get("seen_by", 1)
            corroboration = f" — seen by {seen} scanners" if seen > 1 else ""
            full.append(
                f"- [{card.get('card_type', '?')}/{card.get('evidence_grade', '?')}] "
                f"{card.get('headline', '')} — {card.get('so_what', '')} "
                f"(source: {domain}{corroboration})"
            )
    else:
        full.append("- No cards. Every scanner either found nothing or has no sources configured.")

    full += ["", "## Response plan", ""]
    if plan:
        for item in plan:
            full.append(
                f"- [{item.get('severity', '?')}] {item.get('headline', '')} — "
                f"{item.get('playbook_template', '')}"
            )
    elif plan_ref.get("status") == "no_cards":
        full.append("- No plan: the strategist had no cards to rank.")
    else:
        full.append("- No response plan was produced.")

    exec_lines = [
        "# Executive Edition — competitive intelligence",
        "",
        plan_ref.get("summary")
        or f"{len(cards)} item(s) from {scanners} scanner(s), no response plan.",
        "",
        "## Most urgent",
        "",
    ]
    if plan:
        for item in plan[:3]:
            exec_lines.append(f"- [{item.get('severity', '?')}] {item.get('headline', '')}")
    else:
        for card in cards[:3]:
            exec_lines.append(f"- {card.get('headline', '')}")
    if not plan and not cards:
        exec_lines.append("- Nothing to report this morning.")
    return "\n".join(full), "\n".join(exec_lines)


def morning_brief_rollup_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    """Files the competitive-intelligence brief the eleven scanners feed.

    Distinct from draft-brief's "Morning Brief — {topic}", which is built
    from the ingest path and never sees a scanner card. Two briefs
    because there are two independent branches in this loop, and merging
    them would mean one waiting on the other for no reason; the titles
    say which is which."""
    cards_ref, plan_ref = _collect_rollup_inputs(task_id, db)
    full_body, executive_body = _render_intel_brief(cards_ref, plan_ref)

    with build_vault_client() as vault:
        campaign_id = vault.get_or_create_campaign(
            _campaign_name(envelope), function_id=FUNCTION_ID_SIGNAL_SCORE
        )
        brief = vault.create_brief(
            title="Morning Brief — competitive intelligence",
            body_text=full_body,
            campaign_id=campaign_id,
            function_id=FUNCTION_ID_SIGNAL_SCORE,
        )

    db.set_result_ref(
        task_id,
        {
            "brief_id": brief["id"],
            "campaign_id": campaign_id,
            "card_count": len(cards_ref.get("cards") or []),
            "plan_count": len(plan_ref.get("response_plan") or []),
            "executive_body": executive_body,
        },
    )
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)


def executive_brief_rollup_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    """Files the executive edition rendered by the morning rollup.

    The body was produced upstream, from the same cards and plan, rather
    than re-derived here: two renderings of one morning that could
    disagree is exactly the kind of drift the rest of this work has been
    removing."""
    lineage = resolve_lineage_result(task_id, db)
    if lineage is None:
        raise DispatchError("executive-brief-rollup: no morning-brief-rollup ancestor")
    _ancestor_task, ancestor_ref = lineage
    body = ancestor_ref.get("executive_body")
    if not body:
        raise DispatchError("executive-brief-rollup: ancestor carries no executive_body")

    with build_vault_client() as vault:
        campaign_id = ancestor_ref.get("campaign_id") or vault.get_or_create_campaign(
            _campaign_name(envelope), function_id=FUNCTION_ID_SIGNAL_SCORE
        )
        brief = vault.create_brief(
            title="Executive Edition — competitive intelligence",
            body_text=body,
            campaign_id=campaign_id,
            function_id=FUNCTION_ID_SIGNAL_SCORE,
        )

    db.set_result_ref(
        task_id,
        {
            "brief_id": brief["id"],
            "morning_brief_id": ancestor_ref.get("brief_id"),
            "campaign_id": campaign_id,
        },
    )
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)


def publish_brief_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    """Announces the daily brief, AFTER its QA gate has passed.

    F-BRIEF-ANNOUNCED-BEFORE-QA. draft_brief_handler called
    teams_notify.notify_brief_ready the moment it created the brief --
    before the `qa` task had run at all -- so a brief that then failed QA
    had already been sent to the team, and the block that followed was
    invisible to whoever had read it. This task depends on `qa`, and a
    failed qa never advances its dependents, so announcing here is the
    same notification moved to the point where it is true.

    The notification is best-effort: it no-ops without a webhook, and a
    brief that is in the Vault but unannounced is still a brief."""
    lineage = resolve_lineage_result(task_id, db)
    if lineage is None:
        raise DispatchError("publish-brief: no QA-gate ancestor carries a result_ref")
    _ancestor_task, ancestor_ref = lineage
    brief_id = ancestor_ref.get("brief_id")
    if not brief_id:
        raise DispatchError("publish-brief: QA-gate ancestor result_ref carries no brief_id")

    from orchestrator import teams_notify

    notified = teams_notify.notify_brief_ready(
        title="Morning Brief",
        brief_id=brief_id,
        executive_brief_id=ancestor_ref.get("executive_brief_id") or brief_id,
    )
    log_event(
        logger,
        logging.INFO,
        "brief_published",
        task_id=task_id,
        brief_id=brief_id,
        notified=notified,
    )
    db.set_result_ref(
        task_id,
        {
            "brief_id": brief_id,
            "executive_brief_id": ancestor_ref.get("executive_brief_id"),
            "notified": notified,
        },
    )
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)


# ---------------------------------------------------------------------
# score-signals (F-NO-SCORING)
# ---------------------------------------------------------------------
#
# "Score what matters" is step 2 of the pipeline the README advertises,
# and it did not exist. score-signals fell through to
# legacy_task_pass_through, and opportunity_cards -- a table in the frozen
# vault schema, routed by the Vault API, indexed by campaign -- had NO
# WRITER anywhere in the codebase. draft-brief rendered every signal in
# whatever order the model happened to emit them.
#
# DELIBERATELY DETERMINISTIC, like draft-brief and for the same reason:
# ranking evidence is not a language problem, and inventing an unreviewed
# scoring prompt would put unapproved policy in the daily path (see
# function 48's own header for how that is regarded here). function_id is
# signal.score, mirroring brief.compose -- a real function id with no
# numbered prompt package behind it, because there is no model call.
#
# THE SCORE IS DELIBERATELY SIMPLE AND SAYS SO. It is function 09's own
# `confidence`, mapped to a number. That is the only per-signal quality
# judgement anything in the system currently produces, and it is already
# governed by prompt rules the evals check (never round thin evidence up).
# Anything richer -- pillar weighting, vertical priority, recency decay,
# corroboration across sources -- is business policy that nobody has
# written down, and inventing a weighting here would bury an unreviewed
# opinion in a number that later reads as fact. When that policy exists,
# it belongs in reviewable YAML beside the scan profiles, and this
# function is where it plugs in.

FUNCTION_ID_SIGNAL_SCORE = "signal.score"

# Deliberately coarse. See the block comment above before adding a
# component to this.
CONFIDENCE_SCORES = {"high": 0.8, "medium": 0.5, "low": 0.25}
UNKNOWN_CONFIDENCE_SCORE = 0.1


def _score_signal(signal: dict[str, Any]) -> float:
    return CONFIDENCE_SCORES.get(str(signal.get("confidence", "")), UNKNOWN_CONFIDENCE_SCORE)


def _rank_signals(signal_output: dict[str, Any]) -> list[dict[str, Any]]:
    """Signals highest-score first, ties broken by the order function 09
    emitted them -- a stable sort, so the same batch always ranks the same
    way and a reviewer comparing two runs sees real change rather than
    sort noise."""
    ranked = [
        {
            "headline": str(signal.get("headline", "")),
            "source_url": str(signal.get("source_url", "")),
            "pillar": str(signal.get("pillar", "")),
            "confidence": str(signal.get("confidence", "")),
            "score": _score_signal(signal),
            "position": index,
        }
        for index, signal in enumerate(signal_output.get("signals") or [])
    ]
    ranked.sort(key=lambda item: (-item["score"], item["position"]))
    return ranked


def score_signals_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    lineage = resolve_lineage_result(task_id, db)
    if lineage is None:
        raise DispatchError("score-signals: no ancestor task carries a result_ref to score")
    _ancestor_task, ancestor_ref = lineage
    signal_id = ancestor_ref.get("vault_signal_id")
    if not signal_id:
        raise DispatchError("score-signals: ancestor result_ref carries no vault_signal_id")

    with build_vault_client() as vault:
        campaign_id = vault.get_or_create_campaign(
            _campaign_name(envelope), function_id=FUNCTION_ID_SIGNAL_SCORE
        )
        signal = vault.get_signal(signal_id)
        signal_output = signal.get("payload", {})
        topic = ancestor_ref.get("topic") or signal_output.get("topic", "morning brief")
        ranked = _rank_signals(signal_output)
        if not ranked:
            raise DispatchError(
                f"score-signals: signal batch {signal_id} carries no signals to score"
            )

        agent_run = vault.create_agent_run(
            agent_name=_agent_name("signal-scorer", envelope),
            campaign_id=campaign_id,
            function_id=FUNCTION_ID_SIGNAL_SCORE,
            status="running",
            input_payload={"vault_signal_id": signal_id, "signal_count": len(ranked)},
        )

        for item in ranked:
            card = vault.create_opportunity_card(
                signal_id=signal_id,
                title=item["headline"],
                score=item["score"],
                campaign_id=campaign_id,
                function_id=FUNCTION_ID_SIGNAL_SCORE,
            )
            item["opportunity_card_id"] = card["id"]

        card_ids = [item["opportunity_card_id"] for item in ranked]
        vault.update_agent_run(
            agent_run["id"],
            status="succeeded",
            output_payload={"opportunity_card_ids": card_ids},
            completed_at=_now_iso(),
        )

    with emit_task_span(
        "score-signals",
        function_id=FUNCTION_ID_SIGNAL_SCORE,
        task_ref=task_id,
        model="none",
        cost=0.0,
        run_id=str(envelope.campaign_id),
    ):
        pass  # deterministic scoring only -- no gateway call, no cost

    db.set_result_ref(
        task_id,
        {
            # Superset of what ingest published. score-signals now carries a
            # result_ref, so it -- not ingest -- is what
            # resolve_lineage_result hands draft-brief; these keys must
            # therefore keep answering draft-brief's own questions.
            "vault_signal_id": signal_id,
            "topic": topic,
            "campaign_id": campaign_id,
            "agent_run_id": agent_run["id"],
            "opportunity_card_ids": card_ids,
            "ranking": ranked,
        },
    )
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)


# ---------------------------------------------------------------------
# draft-brief (plan step 9; AC-01, AC-25)
# ---------------------------------------------------------------------

def _order_by_ranking(
    signals: list[dict[str, Any]], ranking: list[dict[str, Any]] | None
) -> list[dict[str, Any]]:
    """Reorder a signal batch to match score-signals' ranking.

    Matched on source_url, the one field carried through both. A signal the
    ranking does not mention keeps its original relative position at the
    end rather than being dropped -- rendering fewer signals than the batch
    contains would be a silent edit, and this function renders, it does not
    curate. No ranking (a run where score-signals produced nothing) leaves
    the batch exactly as it was, which is the pre-scoring behaviour."""
    if not ranking:
        return signals
    order = {item.get("source_url"): index for index, item in enumerate(ranking)}
    unranked = len(order)
    return sorted(
        signals,
        key=lambda signal: order.get(signal.get("source_url"), unranked),
    )


def _render_brief(
    topic: str,
    signal_output: dict[str, Any],
    ranking: list[dict[str, Any]] | None = None,
) -> tuple[str, str]:
    """Deterministic rendering (NO LLM call, per plan step 9) of
    function 09's structured signal batch into a full brief + a
    condensed one-page executive edition. Returns (full_body,
    executive_body).

    `ranking` is score-signals' output when that task ran, so the brief --
    and especially the executive edition's top three -- leads with the
    best-evidenced signals rather than with whatever order the model
    happened to emit. Optional, so a brief rendered without a scoring
    ancestor still renders."""
    summary = signal_output.get("summary", "")
    signals = _order_by_ranking(list(signal_output.get("signals", [])), ranking)

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

        full_body, executive_body = _render_brief(
            topic, signal_output, ancestor_ref.get("ranking")
        )

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

        # F-BRIEF-ANNOUNCED-BEFORE-QA: this used to call
        # teams_notify.notify_brief_ready right here, the moment the brief
        # was created -- before the `qa` task had run at all. A brief that
        # then failed QA had already been sent to the team, and the block
        # that followed was invisible to whoever had read it.
        #
        # The announcement now lives in publish_brief_handler, which
        # depends on `qa` and therefore only runs once the gate has
        # passed: a failed qa never advances its dependents. Same
        # notification, moved to the point where it is true.

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
        qa_payload = {
            "draft_text": draft_text,
            "client_references": client_references,
            "channel": channel,
        }
        _validate_function_input(FUNCTION_ID_02, qa_payload)
        user_content = json.dumps(qa_payload)

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
        declared_pass, violations = _resolve_verdict(FUNCTION_ID_02, verdict)

        # Both halves of check 1: the names the caller declared it intends
        # to use, and the registered names the draft actually contains.
        # The weekly path had only the first and passed it a literal empty
        # list (F-CLEARANCE-CHECK-DEAD); this path passes real references,
        # but a name the model wrote in unasked was invisible to it too.
        uncleared = permission_check.find_uncleared_references(client_references)
        uncleared += permission_check.find_uncleared_in_text(draft_text)
        if uncleared and permission_check.VIOLATION_CODE not in violations:
            violations.append(permission_check.VIOLATION_CODE)
            log_event(
                logger,
                logging.WARNING,
                "qa_uncleared_client_reference_found",
                task_id=task_id,
                names=sorted({clearance.name for clearance in uncleared}),
            )

        passed = not violations
        if passed and not declared_pass:
            # See _single_draft_qa_review's own branch: a refusal with no
            # code is still a refusal. This path runs no
            # reconcile_violations, so there is no false-positive drop to
            # distinguish it from.
            violations = [QA_VERDICT_UNSPECIFIED_FAILURE]
            passed = False
            log_event(
                logger,
                logging.WARNING,
                "qa_verdict_failed_without_violation_code",
                task_id=task_id,
                notes=str(verdict.get("notes", ""))[:200],
            )

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
            draft_excerpt=_teams_display_text(draft_text)[:280],
        )

        return  # never advance_dependents -- request-approval must never see this asset

    passed_ref: dict[str, Any] = {
        "pass": True,
        "vault_asset_id": ancestor_ref.get("vault_asset_id"),
        "brief_id": ancestor_ref.get("brief_id"),
        "content_hash": ancestor_ref.get("content_hash"),
        "draft_task_type": ancestor_task.get("task_type"),
        "review_kind": "brand_steward",
        "agent_run_id": agent_run["id"],
        "campaign_id": campaign_id,
    }
    passed_ref.update(_carried_brief_fields(ancestor_ref))
    db.set_result_ref(task_id, passed_ref)
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

# F-APPROVAL-CARD-BLIND (process 6). request_approval_handler passed
# content_hash and nothing else: preview_title stayed None for every real
# approval (it was set only on the proof-circuit path), and
# evidence_summary was never sent at all. The Gatekeeper's own fallbacks
# then produced, for EVERY content approval ever raised:
#
#   Approval required: publish.social_post (publish)
#   Preview:           publish.social_post (publish)
#   Evidence:          Autonomy policy requires human approval for
#                      publish.social_post / publish. Bound content hash:
#                      3f2a...
#
# Identical for this week's newsletter, last week's carousel and every
# story between them. The "evidence" says why the POLICY requires an
# approval; it says nothing about what is being approved. A human handed
# that card can click approve or reject, but cannot disagree with it --
# there is nothing there to disagree with.
#
# Everything needed was already on the lineage and simply not read. The
# QA gate forwards the draft's pillar, campaign and proof points
# (BRIEF_CARRIED_KEYS) and records its own verdict; the draft records
# which function wrote it. What follows turns that into a card a reviewer
# can actually act on.

APPROVAL_EXCERPT_CHARS = 400


def _approval_subject(draft_task_type: str | None) -> str:
    """A human name for the thing being approved, not a policy key."""
    return {
        "draft-insight-to-story": "Insight-to-story LinkedIn post",
        "draft-executive-ghostwrite": "Executive-voice LinkedIn post",
        "draft-carousel-post": "LinkedIn carousel",
        "draft-newsletter": "Owned-channel newsletter",
        "draft-case-study": "Case study",
        "draft-content-repurpose": "Repurposed social derivatives",
        "draft-brief": "Daily signal brief",
    }.get(draft_task_type or "", "Content asset")


def _approval_preview_title(ancestor_ref: dict[str, Any], draft_task_type: str | None) -> str:
    """One line that distinguishes THIS approval from every other.

    The subject, the pillar it was written to and the week's campaign tag
    -- the three things that differ between two pending cards. Falls back
    cleanly when a field is absent rather than printing "None"."""
    parts = [_approval_subject(draft_task_type)]
    pillar = ancestor_ref.get("pillar")
    if pillar:
        parts.append(str(pillar))
    campaign = ancestor_ref.get("campaign")
    if campaign:
        parts.append(str(campaign))
    return " — ".join(parts)


def _approval_evidence_summary(
    ancestor_ref: dict[str, Any],
    *,
    draft_task_type: str | None,
    draft_excerpt: str | None,
) -> str:
    """What a reviewer needs to disagree: the copy, where its claims came
    from, which reviews passed it, and how the week's subject was chosen.

    Deliberately assembled from what the lineage actually carries, with
    each absence stated rather than skipped -- "no proof points were
    supplied" is a reason to reject, so a card that silently omits the
    line is worse than one that says so."""
    lines: list[str] = []

    if draft_excerpt:
        excerpt = " ".join(draft_excerpt.split())
        if len(excerpt) > APPROVAL_EXCERPT_CHARS:
            excerpt = excerpt[:APPROVAL_EXCERPT_CHARS].rstrip() + "…"
        lines.append(f"Draft: {excerpt}")

    pillar = ancestor_ref.get("pillar")
    pillar_source = ancestor_ref.get("pillar_source")
    if pillar:
        chose = {
            "signals": "chosen from this week's scored signals",
            "rotation": "chosen by the calendar rotation, no signal evidence this week",
        }.get(str(pillar_source), "source not recorded")
        lines.append(f"Pillar: {pillar} ({chose}).")

    proof_points = ancestor_ref.get("proof_points") or []
    if proof_points:
        lines.append(f"Proof points ({len(proof_points)}), each as cited in the brief:")
        for point in proof_points:
            claim = str(point.get("claim", "")).strip()
            source = str(point.get("source", "")).strip()
            lines.append(f"  - {claim} [{source or 'no source recorded'}]")
    else:
        lines.append(
            "Proof points: none supplied. Every claim in this draft traces only to "
            "Canvas's standing approved facts, not to any evidence gathered this week."
        )

    verdicts = ancestor_ref.get("qa_verdicts")
    if verdicts:
        lines.append(f"Reviews passed: {', '.join(verdicts)}.")

    campaign = ancestor_ref.get("campaign")
    if campaign:
        lines.append(f"Attribution: every link carries utm_campaign={campaign}.")

    lines.append(
        f"Approving publishes this asset. Written by {_approval_subject(draft_task_type)}"
        f" ({draft_task_type or 'unknown task type'})."
    )
    return "\n".join(lines)


def _passed_review_kinds(task_id: str, db: Any) -> list[str]:
    """Which of this task's own review gates passed it.

    A Friday task depends on BOTH of its draft's Thursday gates but
    resolve_lineage_result stops at whichever one it reaches first, so the
    single ancestor_ref names only one review. Reading this task's own
    depends_on rows is what lets the card say "Brand Steward and
    fact-check both passed" truthfully instead of naming one and implying
    the other."""
    current = db.get_task(task_id) or {}
    rows = db.get_tasks(current.get("depends_on") or [])
    kinds = []
    for row in rows:
        ref = row.get("result_ref") or {}
        kind = ref.get("review_kind")
        if kind and ref.get("pass") and kind not in kinds:
            kinds.append(kind)
    return kinds


def _draft_excerpt_for_approval(vault: Any, ancestor_ref: dict[str, Any]) -> str | None:
    """The opening of the copy itself, or None when it cannot be read.

    Never fatal: a card missing its excerpt is worse than one without, but
    an approval that dead-letters because the excerpt could not be
    fetched is worse still -- the asset is already reviewed and the hash
    is already bound."""
    vault_asset_id = ancestor_ref.get("vault_asset_id")
    if not vault_asset_id:
        return None
    try:
        asset = vault.get_asset(vault_asset_id)
        text = base64.b64decode(asset["content_base64"]).decode("utf-8")
    except Exception:  # noqa: BLE001 - see docstring: never fatal
        logger.warning("approval_excerpt_unavailable", extra={"asset_id": str(vault_asset_id)})
        return None
    return _reviewable_draft_text(text)


def _approval_card_fields(
    task_id: str,
    db: Any,
    vault: Any,
    ancestor_ref: dict[str, Any],
    *,
    prefix: str | None = None,
) -> dict[str, str]:
    """The three human-facing fields every /gate-check approval carries.

    `prefix` preserves an existing call site's own marker (the loop-proof
    tag, the newsletter's "send NOT yet wired" caveat) in front of the
    generated title, so nothing that a reader already relies on is lost."""
    draft_task_type = ancestor_ref.get("draft_task_type")
    enriched = dict(ancestor_ref)
    enriched["qa_verdicts"] = _passed_review_kinds(task_id, db)
    title = _approval_preview_title(enriched, draft_task_type)
    return {
        "subject": _approval_subject(draft_task_type),
        "preview_title": f"{prefix} {title}" if prefix else title,
        "evidence_summary": _approval_evidence_summary(
            enriched,
            draft_task_type=draft_task_type,
            draft_excerpt=_draft_excerpt_for_approval(vault, ancestor_ref),
        ),
    }


def request_approval_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    """ROUND 34 (10 Aug 2026, confirmed live the night this loop's per-
    draft graph fix finally let a Friday task reach a real /gate-check
    call for the first time): contracts/vault-schema/schema.sql declares
    gate_decisions.agent_run_id NOT NULL FK -> agent_runs -- "the approving
    identity" must be a REAL
    row a handler actually inserted via vault.create_agent_run. This
    handler used to pass envelope.agent_run_id, a synthetic uuid5(event_id,
    source_task_id) the worker computes for tracing only (worker.py line
    ~139) and that NO handler ever writes to agent_runs -- so every real
    gate-check call from this handler was guaranteed to 500 with
    psycopg.errors.ForeignKeyViolation on gate_decisions_agent_run_id_fkey,
    live Postgres FK enforcement that dispatch tests never exercise since
    they mock the gatekeeper HTTP client. Uses the QA-gate ancestor's own
    agent_run_id instead (a real row qa_review_handler/_single_draft_qa_
    review already creates via vault.create_agent_run) -- same fix applied
    to schedule_social_buffer_handler and publish_newsletter_handler
    below."""
    lineage = resolve_lineage_result(task_id, db)
    if lineage is None:
        raise DispatchError("request-approval: no ancestor task carries a result_ref to approve")
    _ancestor_task, ancestor_ref = lineage
    content_hash = ancestor_ref.get("content_hash")
    if not content_hash:
        raise DispatchError("request-approval: ancestor result_ref carries no content_hash")
    approving_agent_run_id = ancestor_ref.get("agent_run_id")
    if not approving_agent_run_id:
        raise DispatchError("request-approval: ancestor result_ref carries no agent_run_id")

    proof_circuit = is_proof_circuit(envelope)
    # preview_reference: the programmatic/API-consumer tag (AC-15).
    # preview_title: the SAME tag, human-visible on the ONE field
    # console/app/templates/approvals.html actually renders (PV3-02) --
    # both are required, neither substitutes for the other.
    preview_reference = f"loop-proof://{task_id}" if proof_circuit else None

    with build_vault_client() as vault:
        card = _approval_card_fields(
            task_id,
            db,
            vault,
            ancestor_ref,
            prefix="[LOOP-PROOF]" if proof_circuit else None,
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
                agent_run_id=str(approving_agent_run_id),
                function_id=REAL_PUBLISH_FUNCTION_ID,
                action_class=REAL_PUBLISH_ACTION_CLASS,
                content_hash=content_hash,
                preview_title=card["preview_title"],
                preview_reference=preview_reference,
                evidence_summary=card["evidence_summary"],
                subject=card["subject"],
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
            "agent_run_id": str(approving_agent_run_id),
            "function_id": REAL_PUBLISH_FUNCTION_ID,
        },
    )
    # Completes as soon as /gate-check responds -- never waits/polls on
    # the human decision (AC-01's bounded scope for this task_type).
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)

# =======================================================================
# PROCESS 9 -- report on cost and performance.
# =======================================================================
#
# infra/modules/scheduling/month-end-reporting-trigger.bicep has been
# firing a `month-end-reporting` heartbeat on the last day of every month
# since it shipped, and NO loop file carries that loop_id. The trigger's
# own header says so in as many words. worker.handle_heartbeat_message
# logs `heartbeat_unknown_loop` and returns an empty list, so every
# month-end since has produced one warning line and nothing else.
#
# The report is DETERMINISTIC -- no model call, no function package.
# Every figure is read back from what the other processes recorded:
# costs metered on each model call, the nightly KPI rollups, the
# attribution outcomes, the publish sweep's own result_refs. A report of
# numbers should not be a model's paraphrase of numbers, and one that
# derives figures nobody else recorded is one nobody can check.
#
# WHAT MAKES IT HONEST. A month-end report that prints "0 posts
# published" while the publisher sits in dry-run is not a report, it is a
# misdiagnosis: it says the marketing failed when the truth is nobody
# turned it on. Every section therefore states not just its number but
# whether that number means anything yet, and the caveats are DERIVED
# from the data rather than hardcoded -- so they disappear on their own
# the moment the underlying gap closes, instead of aging into a lie.

REPORT_FUNCTION_ID = "report.month_end"


def _fmt_money(amount: str | None) -> str:
    try:
        return f"${Decimal(str(amount)):.2f}"
    except (InvalidOperation, TypeError):
        return "$0.00"


def _month_window(today: date) -> tuple[date, date]:
    """The month `today` falls in, as [start, next month start).

    The trigger fires on the last day of the month, so the final hours of
    that day are not yet in the data. Reporting the containing month --
    rather than the previous one -- is what makes the report about the
    month a reader has just lived through; the missing tail is one
    evening, and naming the window in the report itself is what keeps
    that visible rather than implied."""
    start = today.replace(day=1)
    end = date(start.year + 1, 1, 1) if start.month == 12 else date(start.year, start.month + 1, 1)
    return start, end


def _report_caveats(
    costs: dict[str, Any],
    kpis: dict[str, Any],
    attribution: dict[str, Any],
    publishes: dict[str, Any],
) -> list[str]:
    """What this month's numbers cannot yet be read as saying.

    Derived, never hardcoded: each line is emitted only while the
    condition that justifies it holds, so the caveat list shrinks by
    itself as the pipeline is wired up rather than needing an edit that
    someone will forget."""
    caveats: list[str] = []

    statuses = {row["status"]: row["count"] for row in publishes.get("by_status", [])}
    dry_run = statuses.get("published_dry_run", 0)
    live = publishes.get("total", 0) - dry_run
    if dry_run and not live:
        caveats.append(
            f"NOTHING WAS ACTUALLY POSTED. All {dry_run} publish(es) ran in dry-run "
            "(PUBLISHER_DRY_RUN defaults to true and is set nowhere in infra), so every "
            "engagement figure below is necessarily empty. This is a configuration "
            "state, not a marketing result."
        )

    quarantined = attribution.get("quarantined_total", 0)
    if quarantined:
        reasons = ", ".join(
            f"{row['rows']}x {row['reason']}" for row in attribution["quarantined_by_reason"]
        )
        caveats.append(
            f"{quarantined} ingested metric row(s) could not be attributed to any campaign "
            f"({reasons}). Performance figures cover only what did match."
        )
    if not attribution.get("registered_campaigns"):
        # Two different faults wear the same empty map, and telling a
        # reader the wrong one sends them to the wrong place. Registration
        # happens when an asset publishes, so with no publishes the map is
        # empty for the obvious reason; with publishes and still no map,
        # something is actually wrong.
        if publishes.get("total"):
            caveats.append(
                "No campaign is registered in analytics.utm_campaign_map even though "
                f"{publishes['total']} asset(s) published this month, so no metric can "
                "match one. Either those assets carried no campaign tag or the "
                "registration failed — see the publish sweep's own result_refs."
            )
        else:
            caveats.append(
                "No campaign is registered in analytics.utm_campaign_map, so no metric "
                "can match one. Registration happens when an asset publishes, and "
                "nothing published this month."
            )

    if not kpis.get("engagement"):
        caveats.append("No engagement data: no published post carried a matched campaign tag.")
    if not kpis.get("reliability"):
        caveats.append(
            "No publishing-reliability figure: nothing recorded a scheduled post this month."
        )
    if not costs.get("calls"):
        caveats.append("No model calls were metered this month — the loops did not run.")
    return caveats


def _render_month_end_report(
    window: tuple[date, date],
    costs: dict[str, Any],
    kpis: dict[str, Any],
    attribution: dict[str, Any],
    publishes: dict[str, Any],
) -> str:
    start, end = window
    lines = [
        f"# Month-end report — {start:%B %Y}",
        "",
        f"Covering {start:%Y-%m-%d} to {end:%Y-%m-%d} (exclusive). Generated on the "
        "last day of the month, so that day's final hours are not included.",
        "",
        "## Cost",
        "",
        f"Total: {_fmt_money(costs.get('total'))} across {costs.get('calls', 0)} metered "
        "model call(s).",
    ]
    for row in costs.get("by_provider", []):
        lines.append(f"  - {row['provider']}: {_fmt_money(row['amount'])} ({row['calls']} calls)")
    if costs.get("by_agent"):
        lines.append("")
        lines.append("By agent:")
        for row in costs["by_agent"]:
            lines.append(
                f"  - {row['agent_name']}: {_fmt_money(row['amount'])} ({row['calls']} calls)"
            )

    lines += ["", "## Delivery", ""]
    if publishes.get("total"):
        for row in publishes["by_status"]:
            lines.append(f"  - {row['status']}: {row['count']}")
    else:
        lines.append("  - Nothing was published this month.")

    lines += ["", "## Performance", ""]
    if kpis.get("engagement"):
        for row in kpis["engagement"]:
            lines.append(
                f"  - {row['source']}/{row['post_archetype']}: engagement rate "
                f"{row['engagement_rate']} over {row['posts']} post(s)"
            )
    else:
        lines.append("  - No engagement data for this month.")
    for row in kpis.get("reliability", []):
        lines.append(
            f"  - {row['channel']} publishing reliability: {row['published']} published "
            f"of {row['scheduled']} scheduled"
        )
    for row in kpis.get("cost_per_accepted_asset", []):
        lines.append(
            f"  - {row['agent_name']}: {_fmt_money(row['cost'])} across "
            f"{row['accepted_assets']} accepted asset(s)"
        )

    caveats = _report_caveats(costs, kpis, attribution, publishes)
    if caveats:
        lines += ["", "## Read this before the numbers above", ""]
        lines += [f"  - {caveat}" for caveat in caveats]
    return "\n".join(lines)


def report_month_end_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    """Assembles the month-end cost and performance report and files it.

    Filed as a Vault brief because that is where this system already puts
    a document a person is meant to read (draft_brief_handler does the
    same for the daily brief), and notified to Teams best-effort -- the
    notification no-ops without a webhook, and a report that exists in the
    Vault but was not announced is still a report, whereas one that
    dead-letters because Teams is unconfigured is not."""
    window = _month_window(date.today())
    start, end = window
    costs = db.month_costs(start, end)
    kpis = db.month_kpis(start, end)
    attribution = db.month_attribution(start, end)
    publishes = db.month_publishes(start, end)

    body = _render_month_end_report(window, costs, kpis, attribution, publishes)
    title = f"Month-end report — {start:%B %Y}"

    with build_vault_client() as vault:
        campaign_id = vault.get_or_create_campaign(
            _campaign_name(envelope), function_id=REPORT_FUNCTION_ID
        )
        brief = vault.create_brief(
            title=title,
            body_text=body,
            campaign_id=campaign_id,
            function_id=REPORT_FUNCTION_ID,
        )

    from orchestrator import teams_notify

    notified = teams_notify.notify_brief_ready(
        title=title, brief_id=brief["id"], executive_brief_id=brief["id"]
    )

    caveat_count = len(_report_caveats(costs, kpis, attribution, publishes))
    log_event(
        logger,
        logging.INFO,
        "month_end_report_filed",
        month=f"{start:%Y-%m}",
        total_cost=costs.get("total"),
        published=publishes.get("total", 0),
        caveats=caveat_count,
        notified=notified,
    )
    db.set_result_ref(
        task_id,
        {
            "brief_id": brief["id"],
            "campaign_id": campaign_id,
            "month": f"{start:%Y-%m}",
            "total_cost": costs.get("total"),
            "metered_calls": costs.get("calls", 0),
            "published": publishes.get("total", 0),
            "quarantined": attribution.get("quarantined_total", 0),
            "caveats": caveat_count,
            "teams_notified": notified,
        },
    )
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)


# =======================================================================
# PROCESS 7 -- publish. The step that did not exist.
# =======================================================================
#
# Both loops terminated at the approval request. request-approval,
# schedule-social-buffer and publish-newsletter each raise an approval
# card and complete, and NO task in either graph depends on any of them.
# ca-publisher -- a deployed service with a real Buffer path, a gate-token
# verifier and a JTI ledger -- was never called by anything: the
# orchestrator had no publisher client at all. The pipeline's last act was
# to ask a human, and nothing consumed the answer.
#
# WHY THIS IS A SEPARATE LOOP, not a task in the loop that asked.
# request-approval "completes as soon as /gate-check responds -- never
# waits/polls on the human decision (AC-01's bounded scope)". By the time
# anyone clicks Approve, that run is finished. A publish task inside the
# same graph would find "still pending" essentially always. Running on its
# own heartbeat is also what makes an approval granted three days late
# still publish, with no coupling between the governance click surface and
# the orchestrator.
#
# HOW THE TOKEN IS OBTAINED. /gate-check at level 1 with no prior approval
# escalates and returns NO gate token. The second call, after a human has
# approved that exact (agent_run_id, function_id, content_hash) triple,
# finds the approved row via latest_approved() and issues one. So this
# handler calls /gate-check a second time -- that IS the mint step, not a
# redundant policy re-evaluation, and it is also what re-checks the kill
# switch and the policy at publish time rather than trusting a decision
# made when the approval was raised.
#
# WHAT IS DELIBERATELY NOT DONE HERE:
#   * PUBLISHER_DRY_RUN is left alone. It defaults to true and is set
#     nowhere in infra, so the deployed publisher performs no live call.
#     Wiring the publish step and flipping the system to live posting are
#     two separate decisions and this is only the first.
#   * The newsletter is found and declined. app/esp_client.py is a
#     complete, tested Mailchimp/MailerLite adapter that nothing imports
#     except its own test, POST /publish has no ESP branch at all, and no
#     API key, list id or from-address exists in infra. Publishing it
#     would mean building against credentials that do not exist. Declining
#     visibly, on the row, is the honest outcome.

PUBLISH_CANDIDATE_TASK_TYPES = ["schedule-social-buffer", "publish-newsletter"]

# Only the social path has a publisher route. publish.blog_article (the
# newsletter) reaches POST /publish's Buffer branch, which would post an
# email digest to LinkedIn -- so it is filtered out here rather than sent
# to the wrong channel.
PUBLISHABLE_FUNCTION_IDS = {REAL_PUBLISH_FUNCTION_ID}

PUBLISH_STATUS_APPROVED = "approved"

# analytics.scheduled_posts' channel vocabulary is analytics_ingest.rollup's
# own _CHANNEL_TABLE: {"buffer": buffer_post_metrics, "linkedin":
# linkedin_metrics}. The publisher's only live route is Buffer's
# create_draft, so "buffer" is the channel whose observed rows form the
# numerator this denominator is divided into.
PUBLISH_CHANNEL = "buffer"


def _publish_one(
    task: dict[str, Any],
    db: Any,
    vault: Any,
    gatekeeper: Any,
    publisher: Any,
) -> dict[str, Any]:
    """Publish one approved asset, or record why it was not published.

    Returns a summary row; never raises for a single asset's own problem.
    One rejected approval, one unreachable asset or one publisher refusal
    must not stop the other assets in the same sweep from publishing --
    the same reasoning the weekly loop's per-draft gating was rebuilt
    around in round 34.
    """
    ref = task.get("result_ref") or {}
    task_id = task["task_id"]
    outcome: dict[str, Any] = {"task_id": task_id, "task_type": task.get("task_type")}

    function_id = ref.get("function_id")
    agent_run_id = ref.get("agent_run_id")
    content_hash = ref.get("content_hash")
    if not (function_id and agent_run_id and content_hash):
        return {**outcome, "status": "incomplete_result_ref"}

    status = gatekeeper.get_approval_status(
        agent_run_id=agent_run_id, function_id=function_id, content_hash=content_hash
    )
    decision = status.get("status")
    if decision != PUBLISH_STATUS_APPROVED:
        # pending / rejected / expired / not_found are all ordinary, and
        # none of them is this sweep's problem to solve. A pending row is
        # picked up by the next heartbeat; a rejected one never publishes.
        return {**outcome, "status": f"not_approved:{decision}"}

    if function_id not in PUBLISHABLE_FUNCTION_IDS:
        return {**outcome, "status": "no_publish_route", "function_id": function_id}

    vault_asset_id = ref.get("vault_asset_id")
    if not vault_asset_id:
        return {**outcome, "status": "no_vault_asset_id"}
    asset = vault.get_asset(vault_asset_id)
    asset_bytes_b64 = asset["content_base64"]

    # The mint call. Publisher recomputes the hash of the bytes it is sent
    # and compares it with the hash bound into this token, so a token
    # minted for one asset cannot publish another.
    minted = gatekeeper.gate_check(
        agent_run_id=agent_run_id,
        function_id=function_id,
        action_class=REAL_PUBLISH_ACTION_CLASS,
        content_hash=content_hash,
        subject=ref.get("subject"),
    )
    gate_token = minted.get("gate_token")
    if not gate_token:
        # The policy re-evaluated to something other than "approved" at
        # publish time -- the kill switch, a policy edit, or an approval
        # that expired between the status read above and this call.
        return {**outcome, "status": "no_gate_token", "outcome_reported": minted.get("outcome")}

    result = publisher.publish(
        agent_run_id=agent_run_id,
        function_id=function_id,
        asset_bytes_b64=asset_bytes_b64,
        gate_token=gate_token,
        asset_id=vault_asset_id,
    )

    # Written back onto the approving task's own row, which is what
    # find_awaiting_publication() filters on -- this is what stops the
    # next heartbeat re-publishing the same asset.
    # Process 8. Publication is the one moment the campaign slug, the
    # campaign and the asset are all known together, so it is the only
    # place these two lookups can honestly be written. Neither is allowed
    # to fail a publish that has already happened -- the post is live, and
    # a missing map row is a measurement gap the next publish under the
    # same slug repairs, whereas a raise here would leave a published
    # asset marked unpublished and republish it on the next sweep.
    campaign_slug = ref.get("campaign")
    try:
        db.register_utm_campaign(campaign_slug, ref.get("campaign_id"), vault_asset_id)
        db.record_scheduled_post(PUBLISH_CHANNEL)
    except Exception as exc:  # noqa: BLE001 - see comment above
        log_event(
            logger,
            logging.WARNING,
            "analytics_registration_failed",
            task_id=task_id,
            utm_campaign=campaign_slug,
            error=sanitize_exception_text(exc),
        )

    db.set_result_ref(
        task_id,
        {
            **ref,
            "publish_attempt_id": result.get("attempt_id"),
            "publish_status": result.get("status"),
            "publish_reason": result.get("reason"),
            "utm_campaign_registered": bool(campaign_slug),
        },
    )
    return {
        **outcome,
        "status": "published",
        "publish_status": result.get("status"),
        "publish_reason": result.get("reason"),
    }


def publish_approved_assets_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    """Publishes every approved-but-unpublished asset, one sweep."""
    candidates = db.find_awaiting_publication(PUBLISH_CANDIDATE_TASK_TYPES)
    results: list[dict[str, Any]] = []

    if candidates:
        with build_vault_client() as vault:
            with build_gatekeeper_client() as gatekeeper:
                with build_publisher_client() as publisher:
                    for candidate in candidates:
                        try:
                            results.append(
                                _publish_one(candidate, db, vault, gatekeeper, publisher)
                            )
                        except Exception as exc:  # noqa: BLE001 - see _publish_one's docstring
                            log_event(
                                logger,
                                logging.ERROR,
                                "publish_asset_failed",
                                task_id=candidate.get("task_id"),
                                error=sanitize_exception_text(exc),
                            )
                            results.append(
                                {
                                    "task_id": candidate.get("task_id"),
                                    "task_type": candidate.get("task_type"),
                                    "status": "error",
                                }
                            )

    published = [row for row in results if row.get("status") == "published"]
    log_event(
        logger,
        logging.INFO,
        "publish_sweep_completed",
        candidates=len(candidates),
        published=len(published),
    )
    db.set_result_ref(
        task_id,
        {
            "candidates": len(candidates),
            "published": len(published),
            "results": results,
        },
    )
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

# ---------------------------------------------------------------------
# Connecting the daily loop to the weekly one (F-SCORES-UNREAD)
# ---------------------------------------------------------------------
#
# Scoring ranked signals and nothing read the ranking except the order of
# a bullet list in the morning brief. Meanwhile the weekly content loop --
# the one that produces everything Canvas actually publishes -- chose its
# pillar with CONTENT_PILLARS[week_number % 5], an ISO-week rotation that
# read no signal, no card and no score. The two halves of the system were
# not connected: the daily loop could report a market on fire and the
# weekly loop would still write about whatever the calendar said.
#
# Worse, function 41 (Research Brief Writer) received `{"pillar": ...}`
# alone. Its own schema requires `signal_summary`, described as "the raw
# signal or opportunity-card text this brief is built from -- a brief must
# never invent evidence the signal does not supply". It was being asked
# for a CITED brief with no sources, and the five Wednesday drafting
# functions all build on that brief, so every published asset inherited an
# unevidenced base.
#
# Both now read the same recent scored signals, through the same scoring
# rule score-signals itself uses (_score_signal), so there is one
# definition of "what matters" rather than two that can disagree.

RECENT_SIGNAL_LOOKBACK_DAYS = 7
BRIEF_SIGNAL_COUNT = 5


def _recent_scored_signals(
    vault: VaultClientExt, *, days: int = RECENT_SIGNAL_LOOKBACK_DAYS
) -> list[dict[str, Any]]:
    """Every signal recorded in the last `days`, scored with the SAME rule
    score-signals applies, highest first.

    Deliberately re-derived from the signal payloads rather than read back
    from opportunity_cards: a card carries title and score but not the
    pillar (the frozen OpportunityCardCreate contract has no such field),
    and joining cards to signals on a headline string would be a worse
    coupling than recomputing one arithmetic function. opportunity_cards
    remains the queryable projection the console lists; this is the
    decision path.

    Never raises -- a Vault that is unreachable degrades planning to the
    calendar rotation it used before this existed, which is worse but not
    broken."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    scored: list[dict[str, Any]] = []
    try:
        rows = vault.list_signals(limit=RECENT_SIGNAL_SCAN_LIMIT)
    except Exception as exc:  # noqa: BLE001 - see docstring
        log_event(
            logger,
            logging.WARNING,
            "recent_signals_unavailable",
            error=sanitize_exception_text(exc),
        )
        return []

    for row in rows:
        if row.get("signal_type") not in SCAN_BATCH_TYPES:
            continue
        received = _parse_iso_timestamp(row.get("received_at") or row.get("created_at"))
        if received is not None and received < cutoff:
            continue
        payload = row.get("payload") or {}
        for item in _batch_items(payload):
            pillar = str(item.get("pillar", "")).strip()
            if pillar not in CONTENT_PILLARS:
                # Fan-out scanner cards carry a taxonomy, not a pillar.
                # They are real signal but cannot vote on a pillar, so they
                # are skipped here rather than bucketed under a guess.
                continue
            scored.append(
                {
                    "headline": str(item.get("headline", "")),
                    "so_what": str(item.get("so_what", "")),
                    "source_url": str(item.get("source_url", "")),
                    "pillar": pillar,
                    "confidence": str(item.get("confidence", "")),
                    "score": _score_signal(item),
                    "topic": str(payload.get("topic", "")),
                }
            )
    scored.sort(key=lambda item: -item["score"])
    return scored


def _top_pillar(scored: list[dict[str, Any]]) -> str | None:
    """The pillar the week's evidence points at: highest total score across
    its signals, not merely the most numerous, so three low-confidence
    mentions do not outweigh one well-evidenced move. Ties break by
    CONTENT_PILLARS order, so the same evidence always chooses the same
    pillar."""
    if not scored:
        return None
    totals: dict[str, float] = {}
    for item in scored:
        totals[item["pillar"]] = totals.get(item["pillar"], 0.0) + item["score"]
    return max(
        CONTENT_PILLARS,
        key=lambda pillar: (totals.get(pillar, 0.0), -CONTENT_PILLARS.index(pillar)),
    )


def _rotation_pillar() -> str:
    """The pre-existing behaviour, kept as the floor: reproducible, and
    never blocks planning when there is no evidence to plan from."""
    return CONTENT_PILLARS[datetime.now(timezone.utc).isocalendar()[1] % len(CONTENT_PILLARS)]


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

    # F-SCORES-UNREAD: the week's pillar now follows the evidence when
    # there is any, and falls back to the rotation when there is not --
    # so a quiet week or a failed scan never blocks planning, and the
    # previous behaviour remains the floor rather than becoming a new
    # failure mode. Which of the two decided is recorded, because "the
    # market chose this" and "the calendar chose this" are very different
    # claims to make about a week's content.
    with build_vault_client() as vault:
        scored = _recent_scored_signals(vault)
    evidence_pillar = _top_pillar(scored)
    pillar = evidence_pillar or _rotation_pillar()
    pillar_source = "signals" if evidence_pillar else "rotation"
    log_event(
        logger,
        logging.INFO,
        "content_pillar_selected",
        pillar=pillar,
        pillar_source=pillar_source,
        scored_signal_count=len(scored),
        week_number=week_number,
    )

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
            "pillar_source": pillar_source,
            "scored_signal_count": len(scored),
            # The evidence this week's plan rests on, carried forward so
            # function 41 builds its brief from the same signals that
            # chose the pillar rather than fetching its own view.
            "top_signals": [item for item in scored if item["pillar"] == pillar][
                :BRIEF_SIGNAL_COUNT
            ],
        },
    )
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)

# function 41's schema requires a `vertical` from a five-value enum, and
# nothing in the system can currently derive one honestly. Signals from
# the market-intelligence profile are sector-agnostic, and the six
# vertical profiles that WOULD name one carry no source urls yet, so they
# produce nothing to read. Rotating is a placeholder, not a judgement --
# it is recorded as such on the agent_run and the result_ref so nobody
# mistakes it for evidence, and it resolves itself the moment a vertical
# profile is sourced and its signals start carrying a real sector.
BRIEF_VERTICALS = [
    "logistics & distribution",
    "mining & industrial",
    "beverage/FMCG",
    "construction",
    "financial services",
]


def _monday_plan(task_id: str, db: Any) -> dict[str, Any]:
    """The whole plan-content-monday result_ref, not just its pillar --
    function 41 needs the evidence that chose the pillar, not only the
    name of it."""
    lineage = resolve_lineage_result(task_id, db)
    if lineage is None:
        raise DispatchError(
            f"{task_id}: no ancestor task carries a result_ref for this week's plan"
        )
    _ancestor_task, ancestor_ref = lineage
    return ancestor_ref


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
    plan = _monday_plan(task_id, db)
    pillar = plan.get("pillar")
    if not pillar:
        raise DispatchError("draft-research-brief: monday plan carries no pillar")
    top_signals = plan.get("top_signals") or []
    vertical = BRIEF_VERTICALS[
        int(plan.get("week_number") or 0) % len(BRIEF_VERTICALS)
    ]

    # F-BRIEF-WITHOUT-EVIDENCE: this handler used to send `{"pillar": ...}`
    # and nothing else, while schema.json requires `signal_summary` -- the
    # field whose own description says a brief "must never invent evidence
    # the signal does not supply". A cited brief was being requested with
    # no sources, and the five Wednesday drafting functions all build on
    # it. The week's actual scored signals now go in, attributed.
    if top_signals:
        signal_summary = "\n".join(
            f"- [{item.get('confidence', '?')}] {item.get('headline', '')} — "
            f"{item.get('so_what', '')} (source: {item.get('source_url', '')})"
            for item in top_signals
        )
    else:
        # Said plainly rather than left blank: the prompt's own rules turn
        # an absence of evidence into a low-confidence brief, which is the
        # honest output for a week the scan found nothing in.
        signal_summary = (
            f"No scored market signals were recorded for the {pillar} pillar in the "
            "last 7 days. Write from Canvas's own positioning only, cite nothing that "
            "is not supplied here, and say plainly that this week produced no new "
            "market evidence."
        )

    with build_vault_client() as vault:
        campaign_id = vault.get_or_create_campaign(
            _campaign_name(envelope), function_id=FUNCTION_ID_41
        )
        agent_run = vault.create_agent_run(
            agent_name=_agent_name("research-brief-writer", envelope),
            campaign_id=campaign_id,
            function_id=FUNCTION_ID_41,
            status="running",
            input_payload={
                "pillar": pillar,
                "vertical": vertical,
                "vertical_source": "rotation-placeholder",
                "pillar_source": plan.get("pillar_source"),
                "signal_count": len(top_signals),
            },
        )

        system_prompt = _read_prompt("41-research-brief-writer")
        payload = {"pillar": pillar, "vertical": vertical, "signal_summary": signal_summary}
        _validate_function_input(FUNCTION_ID_41, payload)
        user_content = json.dumps(payload)

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
        # F-WEEKLY-OUTPUT-UNVALIDATED: the daily loop validates what its
        # functions return; the weekly loop did not, so this brief -- the
        # artifact every Wednesday draft is built from -- could be any
        # shape at all and still be written to the Vault.
        _validate_function_output(FUNCTION_ID_41, output)
        vault.update_agent_run(
            agent_run["id"], status="succeeded", output_payload=output, completed_at=_now_iso()
        )
        brief = vault.create_brief(
            title=f"Research Brief — {pillar}",
            body_text=json.dumps(output.get("brief", output)),
            campaign_id=campaign_id,
            function_id=FUNCTION_ID_41,
        )

    brief_body = output.get("brief", {})
    proof_points = brief_body.get("proof_points") or []
    if not proof_points:
        # Not an error: function 41's own schema says this array is "empty
        # when the signal supplies no citable evidence -- proof over
        # platitude means an unsupported claim is never fabricated to fill
        # this array". An empty week is the honest outcome of a week with
        # no evidence, and saying so here is what lets a reader tell that
        # apart from a brief nobody checked.
        log_event(
            logger,
            logging.WARNING,
            "research_brief_without_proof_points",
            pillar=pillar,
            signal_count=len(top_signals),
        )

    db.set_result_ref(
        task_id,
        {
            "brief_id": brief["id"],
            "pillar": brief_body.get("pillar", pillar),
            "vertical": brief_body.get("vertical"),
            "audience_note": output.get("audience_note"),
            # F-PROOF-POINTS-DROPPED: function 41 PRODUCES structured
            # {claim, source} proof points -- its output schema requires
            # the array -- and the drafting handoff flattened the whole
            # brief to a JSON string, so five consumers whose own schemas
            # require `proof_points` or `proof_point` had to re-infer them
            # from prose. Carried structurally here so the drafting stage
            # can be given what it actually asks for.
            "proof_points": proof_points,
            "proof_point_count": len(proof_points),
            "signal_count": len(top_signals),
            "pillar_source": plan.get("pillar_source"),
            "agent_run_id": agent_run["id"],
            "campaign_id": campaign_id,
        },
    )
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)

# The brief-shaped fields draft_research_brief_handler writes onto its own
# result_ref above, which every Wednesday drafting handler needs and which
# any pass-through task standing between the brief and those drafts must
# therefore carry forward.
#
# F-BRIEF-FIELDS-DROPPED-BY-QA: inserting tuesday-qa-research-brief between
# the brief and the six drafts (the process-3 review gate) silently starved
# them. resolve_lineage_result stops at the FIRST ancestor carrying any
# non-null result_ref, so the walk began stopping at the QA task, whose
# result_ref forwarded `brief_id` and `vault_asset_id` but none of these.
# Every draft's `pillar` became None -- which draft_content_repurpose_
# handler's own "source ancestor result_ref carries no pillar" guard would
# then have raised on, one hop further down. Not caught by the loop tests:
# they assert graph shape, and no test walks a brief through a review into
# a draft. Adding a node to a graph whose edges carry untyped dicts is
# exactly the failure mode result_ref's shapelessness invites.
BRIEF_CARRIED_KEYS = (
    "pillar",
    "vertical",
    "audience_note",
    "proof_points",
    "proof_point_count",
    "signal_count",
    "pillar_source",
    # `campaign` does not come from the brief -- it is derived at drafting
    # from the pillar -- but it travels the same hops and was dropped at
    # the same gate, so it belongs in the same list.
    #
    # F-CAMPAIGN-DROPPED-BY-QA: it was absent here, so the QA gate carried
    # `pillar` forward and left `campaign` behind. Two consequences, one
    # per downstream stage. The approval card lost the tag from its title
    # and its whole "Attribution: every link carries utm_campaign=..."
    # line, and the publish step had no slug to register -- which is the
    # exact join key process 8's measurement depends on, so every ingested
    # metric row would have quarantined as unmatched even after the map
    # got a writer.
    #
    # Not caught by the process-6 tests because they constructed the
    # gate's result_ref by hand rather than producing it through a real
    # draft, so the field was present in the fixture and absent in life.
    # test_campaign_survives_the_qa_gate walks the real path instead.
    "campaign",
)


def _carried_brief_fields(ancestor_ref: dict[str, Any]) -> dict[str, Any]:
    """Copies whatever BRIEF_CARRIED_KEYS the ancestor actually has, so a
    pass-through task neither drops them nor invents nulls for a lineage
    (the daily loop's own qa-review) that never had a brief to begin
    with."""
    return {key: ancestor_ref[key] for key in BRIEF_CARRIED_KEYS if key in ancestor_ref}

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
    build_payload: Any,
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
    _single_draft_qa_review) before it can ever reach approval. Setting
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

    # Built and validated BEFORE any vault work, so a function that cannot
    # honestly be called this week costs nothing and leaves no half-open
    # campaign or running agent_run behind it.
    try:
        payload = build_payload(ancestor_ref)
    except DraftNotAttempted as skip:
        _complete_undrafted(
            task_id, db, task_name=task_name, function_id=function_id, skip=skip
        )
        return
    _validate_function_input(function_id, payload)

    with build_vault_client() as vault:
        campaign_id = vault.get_or_create_campaign(
            _campaign_name(envelope), function_id=function_id
        )

        agent_run = vault.create_agent_run(
            agent_name=_agent_name(agent_name, envelope),
            campaign_id=campaign_id,
            function_id=function_id,
            status="running",
            input_payload={"brief_id": brief_id, **payload},
        )

        system_prompt = _read_prompt(prompt_dir)
        user_content = json.dumps(payload)

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
        # Both halves of the contract now hold for every Wednesday draft:
        # the payload above was checked against schema.json's input, this
        # checks what came back against its output. render_draft_text
        # reads fields straight off `output` (a carousel's slide array, a
        # newsletter's subject and body), so an off-contract response
        # otherwise renders a quietly empty asset that reads as a real
        # draft all the way to Thursday's review.
        _validate_function_output(function_id, output)
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
            "pillar": payload["pillar"],
            "campaign": payload["campaign"],
            # Carried one hop further than the draft needs it: Thursday's
            # fact-check resolves lineage to THIS task, and function 48's
            # List D is the {claim, source} evidence this draft was built
            # from. Without it here the check falls back to the standing
            # lists and calls the week's real, cited evidence fabricated.
            "proof_points": ancestor_ref.get("proof_points") or [],
        },
    )
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)

# F-CAMPAIGN-TAG-INVENTED: every Wednesday drafting prompt requires the
# single call-to-action link to carry utm_source/utm_medium/utm_campaign
# ("39-insight-to-story-editor/prompt.md" line 133 and its four siblings),
# and every one of those functions' schema.json requires a `campaign`
# slug -- which the shared handler never sent. Six drafting models each
# invented their own utm_campaign for the same week's brief, so one
# week's assets carried six unrelated attribution tags and process 8
# ("measure") had nothing coherent to attribute to.
#
# Derived from the pillar rather than generated, so every asset built on
# one week's brief shares one tag, and the same pillar always produces the
# same tag. This is draft_content_repurpose_handler's own existing
# derivation, lifted to a shared helper so the six drafts and the
# repurposer cannot drift apart -- function 52 already did this correctly
# and alone.
def _campaign_slug(pillar: str) -> str:
    """Every CONTENT_PILLARS value maps to a slug matching the pattern all
    six schemas impose on `campaign` (^[a-z0-9]+(-[a-z0-9]+)*$)."""
    return pillar.lower().replace(" ", "-")


class DraftNotAttempted(Exception):
    """Raised by a Wednesday drafting payload builder when this week's
    evidence cannot honestly fill that function's required input fields.

    Not a failure. Two of the six drafting functions require facts nothing
    in the system holds -- function 43 an `executive_name`, function 47 a
    real client engagement's situation/approach/result -- and a third and
    fourth (45, 46) require at least one proof point to build from. The
    alternative to raising here is putting a placeholder into a required
    field, which for a ghostwritten executive voice or a case study means
    publishing a fabrication under someone's name. Pieter's standing
    direction (1 Sep 2026): no executive and no client engagement is to be
    named yet.

    The task completes rather than fails: nothing went wrong, there was
    simply nothing this function could truthfully be asked to write."""

    def __init__(self, status: str, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


def _complete_undrafted(
    task_id: str,
    db: Any,
    *,
    task_name: str,
    function_id: str,
    skip: DraftNotAttempted,
) -> None:
    """Terminal state for a drafting task that was deliberately not
    attempted: COMPLETED with a result_ref that says so and carries no
    vault_asset_id, which _single_draft_qa_review reads to distinguish
    'nothing was written on purpose' from 'a draft went missing'."""
    log_event(
        logger,
        logging.WARNING,
        "draft_not_attempted",
        task_name=task_name,
        function_id=function_id,
        status=skip.status,
        reason=skip.reason,
    )
    db.set_result_ref(
        task_id,
        {
            "status": skip.status,
            "function_id": function_id,
            "reason": skip.reason,
        },
    )
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)


def _proof_point_strings(ancestor_ref: dict[str, Any]) -> list[str]:
    """Flattens function 41's structured {claim, source} proof points into
    the plain strings functions 39/45/46 declare (`minLength: 10`).

    The source is kept, not dropped: "proof over platitude" is the whole
    point of carrying these, and a drafting function that cannot see where
    a claim came from cannot honour its own never-fabricate rule."""
    flattened = []
    for point in ancestor_ref.get("proof_points") or []:
        claim = str(point.get("claim", "")).strip()
        source = str(point.get("source", "")).strip()
        if not claim:
            continue
        flattened.append(f"{claim} (source: {source})" if source else claim)
    return flattened


def _require_pillar(task_name: str, ancestor_ref: dict[str, Any]) -> str:
    pillar = ancestor_ref.get("pillar")
    if not pillar:
        raise DispatchError(f"{task_name}: ancestor result_ref carries no pillar")
    return str(pillar)


NO_EVIDENCE_PROOF_POINT = (
    "No documented proof point was available for this week's brief - "
    "flag the evidence gap rather than making a claim."
)


def _build_insight_story_payload(ancestor_ref: dict[str, Any]) -> dict[str, Any]:
    """Function 39 takes ONE proof point. Where 45 and 46 must decline an
    evidence-free week, 39's own schema names the behaviour it wants
    instead -- `proof_point` is documented as "When no evidence has been
    documented yet, state that plainly here -- the editor must flag the
    gap rather than fabricate a proof point." So the gap statement below
    is the contract's own instruction, not a placeholder smuggled into a
    required field, and prompt.md line 120 has the matching rule on the
    model's side ("If the supplied `proof_point` plainly states that no
    evidence...")."""
    pillar = _require_pillar("draft-insight-to-story", ancestor_ref)
    proof_points = _proof_point_strings(ancestor_ref)
    payload: dict[str, Any] = {
        "pillar": pillar,
        "proof_point": proof_points[0] if proof_points else NO_EVIDENCE_PROOF_POINT,
        "campaign": _campaign_slug(pillar),
    }
    audience_note = ancestor_ref.get("audience_note")
    if audience_note:
        payload["audience_note"] = audience_note
    return payload


def _build_multi_proof_payload(
    task_name: str, ancestor_ref: dict[str, Any], *, max_points: int
) -> dict[str, Any]:
    """Functions 45 and 46 both require `proof_points` with `minItems: 1`
    and no gap-statement clause of 39's kind: a carousel is one proof point
    per slide, a newsletter is this week's proof points. With none, there
    is no honest call to make -- so the week produces no carousel and no
    newsletter rather than a fabricated one. The bound differs (6 slides
    vs 5 newsletter points), so it is passed in rather than assumed."""
    pillar = _require_pillar(task_name, ancestor_ref)
    proof_points = _proof_point_strings(ancestor_ref)
    if not proof_points:
        raise DraftNotAttempted(
            "no_evidence",
            f"{task_name}: this week's research brief carries no proof points, and "
            "this function's schema requires at least one -- declining to draft "
            "rather than fabricate evidence",
        )
    return {
        "pillar": pillar,
        "proof_points": proof_points[:max_points],
        "campaign": _campaign_slug(pillar),
    }


def _build_carousel_payload(ancestor_ref: dict[str, Any]) -> dict[str, Any]:
    return _build_multi_proof_payload("draft-carousel-post", ancestor_ref, max_points=6)


def _build_newsletter_payload(ancestor_ref: dict[str, Any]) -> dict[str, Any]:
    return _build_multi_proof_payload("draft-newsletter", ancestor_ref, max_points=5)


def _build_ghostwrite_payload(ancestor_ref: dict[str, Any]) -> dict[str, Any]:
    """Function 43 requires `executive_name` -- the person whose voice the
    piece is written in. Nothing in this repository supplies one: the
    string appears only inside 43's own package (schema, skill, evals,
    tool_check) and in no config, register or positioning document. A
    placeholder here is a real opinion attributed to a real person who
    never said it, which is the exact failure 43's own never-fabricate
    rule exists to prevent."""
    _require_pillar("draft-executive-ghostwrite", ancestor_ref)
    raise DraftNotAttempted(
        "no_executive_configured",
        "draft-executive-ghostwrite: function 43 requires executive_name and no "
        "executive has been configured -- a ghostwritten voice needs a real "
        "person, not a placeholder",
    )


def _build_case_study_payload(ancestor_ref: dict[str, Any]) -> dict[str, Any]:
    """Function 47 requires situation/approach/result -- a real client
    engagement. docs/permission-register.yaml is default-deny and nothing
    in it is CLEARED (Imperial and Rotork both UNCLEARED, no written
    permission held), so there is no engagement this may be written from,
    and a research brief is not one. The loop already excludes case
    studies from Friday's auto-schedule for the same reason; this closes
    the drafting side."""
    _require_pillar("draft-case-study", ancestor_ref)
    raise DraftNotAttempted(
        "no_cleared_engagement",
        "draft-case-study: function 47 requires a real engagement's "
        "situation/approach/result and no client engagement is cleared in "
        "docs/permission-register.yaml -- declining to invent one",
    )


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
        build_payload=_build_insight_story_payload,
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
        build_payload=_build_ghostwrite_payload,
        max_tokens=2560,
    )

CAROUSEL_BULK_CSV_MARKER = "--- canva_bulk_create_csv ---"


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
    lines.append(CAROUSEL_BULK_CSV_MARKER)
    lines.append(output.get("canva_bulk_create_csv", ""))
    return "\n".join(lines)


def _teams_display_text(draft_text: str) -> str:
    """Strips _render_carousel's appended Canva bulk-upload CSV block
    before draft_text feeds a Teams excerpt or a diff between retry
    attempts (F-CAROUSEL-CSV-LEAKS-INTO-TEAMS, 15 Aug 2026). The CSV is
    machine-oriented (slide/image/template-id columns for Canva's bulk
    creator) and no code anywhere parses it back out of draft_text --
    confirmed via repo-wide search, only _render_carousel ever writes it
    -- so it is safe to drop for display purposes. Left in place for
    non-carousel draft_text (the marker is absent, find() returns -1,
    text passes through unchanged) and for every OTHER consumer of the
    stored draft_text (Vault, the console review page, the approval
    inbox) -- only what feeds Teams notify is touched here. Confirmed
    live: Pieter's screenshots (15 Aug) of a QA-retry-exhausted carousel
    card showed raw CSV rows and unified-diff hunk headers dumped
    verbatim into the "excerpt"/"track changes" TextBlocks -- unreadable
    to a human reviewer -- because the pre-fix diff/excerpt ran over the
    full CSV-appended text instead of just the slide copy."""
    idx = draft_text.find(CAROUSEL_BULK_CSV_MARKER)
    return draft_text[:idx].rstrip() if idx != -1 else draft_text

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
        build_payload=_build_carousel_payload,
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
        build_payload=_build_newsletter_payload,
        max_tokens=3584,
    )

def _render_case_study(output: dict[str, Any]) -> str:
    return json.dumps(output.get("case_study", output), indent=2)

def draft_case_study_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    """Case studies are intentionally excluded from Friday's auto-schedule
    (function 47's own prompt.md: human-initiated cadence only, never
    published without a CLEARED client) -- weekly-content-loop.yaml
    deliberately has no friday-schedule-social-buffer-* task depending on
    wednesday-draft-case-study's Thursday review tasks (round 34's
    per-draft graph; see docs/content-learnings.md)."""
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
        build_payload=_build_case_study_payload,
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
    # Both sources skipped (a week with no proof points writes no
    # newsletter, and the case study is never drafted while no engagement
    # is cleared) -- there is genuinely nothing to repurpose, which is a
    # completed no-op rather than the dead letter a DispatchError raises.
    if all((row.get("result_ref") or {}).get("status") for row in rows.values()):
        raise DraftNotAttempted(
            "no_source_asset",
            "draft-content-repurpose: neither draft-newsletter nor draft-case-study "
            "produced an asset this week -- nothing to repurpose",
        )
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
    try:
        source_task, source_ref = _select_repurpose_source(task_id, db)
    except DraftNotAttempted as skip:
        _complete_undrafted(
            task_id,
            db,
            task_name="draft-content-repurpose",
            function_id=FUNCTION_ID_52,
            skip=skip,
        )
        return
    vault_asset_id = source_ref["vault_asset_id"]
    pillar = source_ref.get("pillar")
    if not pillar:
        raise DispatchError(
            "draft-content-repurpose: source ancestor result_ref carries no pillar"
        )
    campaign_slug = _campaign_slug(pillar)

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
# Thursday per-draft QA gates (qa-review-brand-steward, qa-review-fact-
# check) -- see module docstring for why these are NOT a generalisation
# of qa_review_handler despite the structural similarity.
# ---------------------------------------------------------------------

# Round 34 (docs/content-learnings.md): weekly-content-loop.yaml now has
# ONE Thursday review task per Wednesday draft per review_kind (12 total,
# was 2 aggregate tasks covering all 6 drafts each). This replaces the old
# _aggregate_qa_review / QA_REVIEWED_DRAFT_TASK_TYPES / _gather_sibling_
# drafts machinery, which reviewed all 6 drafts inside ONE task and
# resolved that task to a single all-or-nothing terminal state -- so one
# bad draft (a spelling typo, say) dead-lettered friday-schedule-social-
# buffer and friday-publish-newsletter for every OTHER draft too, even
# ones that individually passed both reviews cleanly. See the round-34
# "batch-gating" finding in docs/content-learnings.md for the full
# incident history (confirmed live: 4-5 of 6 drafts individually clean,
# both Friday tasks cascade-dead-lettered anyway on the other 1-2).

# ---------------------------------------------------------------------
# F-QA-RETRY-LOOP (11 Aug 2026) -- reopened qa-feedback-loop-proposal-
# 2026-08-05.md per Pieter's explicit redesign (see
# claude_qa-feedback-loop-proposal-b-v2-2026-08-11.md for the full spec
# this implements). The non-negotiable invariant, stated plainly: every
# one of the 6 Wednesday drafts must reach Teams every night, whether it
# passed QA cleanly, passed after an automated retry, or exhausted every
# retry -- content-quality safeguards (the anti-hollowing check below)
# must NEVER be the reason a draft goes missing from Teams. Phase 1
# (bounded regenerate-and-recheck) and Phase 2a (a "retries exhausted"
# Teams card carrying a track-changes diff) are implemented here. Phase
# 2b (a human's Teams comment triggering exactly one more retry) and
# Phase 3 (routing an unresolved draft into governance.approval_requests)
# are DELIBERATELY NOT implemented in this change -- both need a new
# inbound API surface (something to receive a Teams Action.Submit /
# adaptive-card-input callback) that does not exist anywhere in this
# codebase yet, and are scoped out explicitly rather than half-built. See
# this PR's description for the follow-up.
# ---------------------------------------------------------------------

MAX_QA_RETRY_ATTEMPTS = 10

# Per-review-kind identity, mirroring qa_review_brand_steward_handler /
# qa_review_fact_check_handler's own kwargs to _single_draft_qa_review --
# duplicated here (not imported from those two thin wrappers, which pass
# these as call-site literals rather than a lookup table) so the retry
# loop can run EITHER review kind against a regenerated draft without
# needing a live TaskEnvelope/task_id for the kind it didn't start from.
_QA_REVIEW_PARAMS: dict[str, dict[str, str]] = {
    "brand_steward": {
        "task_type": "qa-review-brand-steward",
        "function_id": FUNCTION_ID_02,
        "prompt_dir": "02-brand-steward-qa",
        "agent_name": "brand-steward-qa",
    },
    "fact_check": {
        "task_type": "qa-review-fact-check",
        "function_id": FUNCTION_ID_48_FACT_CHECK,
        "prompt_dir": "48-fact-check-verdict",
        "agent_name": "fact-check-verdict",
    },
}

# Regeneration recipe per Wednesday draft task_type -- one entry per
# _draft_social_post_handler-routed drafting handler (see each one's own
# definition above for where these values come from). draft-content-
# repurpose is DELIBERATELY excluded: it has its own dedicated handler
# with a different lineage mechanism (two source-draft depends_on
# entries, not a research-brief walk -- see _select_repurpose_source's
# docstring), and teaching the retry loop that second shape is scoped
# out of this change. A draft type absent from this table falls back to
# the pre-retry-loop, single-shot behaviour untouched -- see
# _single_draft_qa_review's own not-passed branch below.
_DRAFT_REGEN_PARAMS: dict[str, dict[str, Any]] = {
    "draft-insight-to-story": {
        "function_id": FUNCTION_ID_39,
        "prompt_dir": "39-insight-to-story-editor",
        "agent_name": "insight-to-story-editor",
        "asset_type": "linkedin_post",
        "render_draft_text": _render_simple_post,
        "max_tokens": 2048,
    },
    "draft-executive-ghostwrite": {
        "function_id": FUNCTION_ID_43,
        "prompt_dir": "43-executive-ghostwriter",
        "agent_name": "executive-ghostwriter",
        "asset_type": "linkedin_post",
        "render_draft_text": _render_simple_post,
        "max_tokens": 2560,
    },
    "draft-carousel-post": {
        "function_id": FUNCTION_ID_45,
        "prompt_dir": "45-carousel-post-writer",
        "agent_name": "carousel-post-writer",
        "asset_type": "carousel_post",
        "render_draft_text": _render_carousel,
        "max_tokens": 2560,
    },
    "draft-newsletter": {
        "function_id": FUNCTION_ID_46,
        "prompt_dir": "46-newsletter-writer",
        "agent_name": "newsletter-writer",
        "asset_type": "newsletter",
        "render_draft_text": _render_newsletter,
        "max_tokens": 3584,
    },
    "draft-case-study": {
        "function_id": FUNCTION_ID_47,
        "prompt_dir": "47-case-study-writer",
        "agent_name": "case-study-writer",
        "asset_type": "case_study",
        "render_draft_text": _render_case_study,
        "max_tokens": 4096,
    },
}


def _looks_hollowed(original: str, revised: str) -> bool:
    """Best-effort, NON-BLOCKING signal only (Pieter's explicit 11 Aug
    2026 ruling: "if deletion is better than 10 retries deletion is
    better" -- i.e. this NEVER gates or stops a retry attempt; it only
    gets logged and surfaced on the retries-exhausted Teams card so a
    human reviewer knows to look closely at what changed). Flags a
    revision that dropped the canvasintelligence.com URL the original
    carried, shrank by more than ~40%, or lost every digit the original
    had (a crude proxy for "lost its proof points") -- any one of which
    is a plausible sign a retry attempt fixed a QA violation by deleting
    content rather than rewriting it."""
    if "canvasintelligence.com" in original and "canvasintelligence.com" not in revised:
        return True
    if len(original) > 40 and len(revised) < len(original) * 0.6:
        return True
    if re.search(r"\d", original) and not re.search(r"\d", revised):
        return True
    return False


def _finalize_qa_failure(
    db: Any,
    task_id: str,
    *,
    review_kind: str,
    violations: list[str],
    draft_task: dict[str, Any],
    draft_text: str,
    agent_run_id: str | None = None,
    campaign_id: str | None = None,
) -> None:
    """The pre-retry-loop single-shot failure outcome (set_result_ref +
    FAILED/QA_BLOCKED + notify_needs_edit), extracted unchanged from
    _single_draft_qa_review's own not-passed branch so it can be reused
    by every path that ends in this one outcome: a NEVER_RETRYABLE
    violation, a draft_task_type the retry loop doesn't know how to
    regenerate, and a task that lost the sibling-ownership race for its
    draft's advisory lock."""
    db.set_result_ref(
        task_id,
        {
            "pass": False,
            "violations": violations,
            "draft_task_id": draft_task["task_id"],
            "draft_task_type": draft_task.get("task_type"),
            "agent_run_id": agent_run_id,
            "campaign_id": campaign_id,
        },
    )
    db.transition(task_id, TaskStateEnum.FAILED, TransitionReason.QA_BLOCKED)
    log_event(
        logger,
        logging.INFO,
        "qa_review_blocked",
        task_id=task_id,
        review_kind=review_kind,
        draft_task_id=draft_task["task_id"],
        violations=violations,
    )
    from orchestrator import teams_notify

    teams_notify.notify_needs_edit(
        task_id=task_id,
        channel="linkedin",
        violations=violations,
        draft_excerpt=_teams_display_text(draft_text)[:280],
    )


def _regenerate_draft_content(
    *,
    vault: VaultClientExt,
    gateway: OrchestratorGatewayClient,
    envelope: TaskEnvelope,
    campaign_id: str,
    function_id: str,
    prompt_dir: str,
    agent_name: str,
    render_draft_text: Any,
    max_tokens: int,
    brief_body: Any,
    pillar: Any,
    vertical: Any,
    audience_note: Any,
    previous_draft_text: str,
    violations: list[str],
    attempt: int,
) -> tuple[str, dict[str, Any]]:
    """One regeneration attempt for the QA retry loop. Same completion
    shape _draft_social_post_handler uses to draft the first time, plus a
    `revision_feedback` field naming exactly which QA violations to fix
    and an explicit anti-hollowing instruction. Sent as DATA (part of
    user_content), not a prompt.md edit -- a per-attempt violation list
    is per-attempt data, not a static policy the prompt file should
    hardcode.

    Deliberately has NO side effect on task_state: unlike
    _draft_social_post_handler (which both drafts AND completes its own
    task), this only produces text -- the caller owns creating the Vault
    asset and deciding what happens to the draft task's result_ref, since
    a retry attempt must never itself fire advance_dependents."""
    agent_run = vault.create_agent_run(
        agent_name=_agent_name(agent_name, envelope),
        campaign_id=campaign_id,
        function_id=function_id,
        status="running",
        input_payload={
            "pillar": pillar,
            "retry_attempt": attempt,
            "revision_violations": violations,
        },
    )
    system_prompt = _read_prompt(prompt_dir)
    user_content = json.dumps(
        {
            "brief": brief_body,
            "pillar": pillar,
            "vertical": vertical,
            "audience_note": audience_note,
            "revision_feedback": {
                "previous_draft": previous_draft_text,
                "violations_to_fix": violations,
                "instruction": (
                    "This is a revision, not a new draft. The previous draft above "
                    "failed QA review on exactly the violations listed. Fix only "
                    "those specific issues. Do not remove the call to action, do "
                    "not remove or alter the canvasintelligence.com URL, do not "
                    "drop any proof point, statistic, or named product/partner "
                    "reference that was not itself flagged. Keep the output "
                    "approximately the same length and shape as the previous draft "
                    "unless a flagged violation requires otherwise."
                ),
            },
        }
    )
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
    output = _parse_json_content(response["content"])
    draft_text = render_draft_text(output)
    vault.update_agent_run(
        agent_run["id"], status="succeeded", output_payload=output, completed_at=_now_iso()
    )
    return draft_text, agent_run


def _run_single_qa_check(
    *,
    vault: VaultClientExt,
    gateway: OrchestratorGatewayClient,
    envelope: TaskEnvelope,
    campaign_id: str,
    draft_text: str,
    review_kind: str,
    permission_check_module: Any,
) -> tuple[list[str], str]:
    """Runs ONE QA completion (brand_steward or fact_check) against
    draft_text and returns (reconciled_violations, agent_run_id) --
    shares _single_draft_qa_review's exact verdict/reconciliation logic
    (permission_check + brand_rules.reconcile_violations) so a retry
    attempt is graded by the identical rule set an ordinary Thursday
    review uses, never a looser or different check."""
    params = _QA_REVIEW_PARAMS[review_kind]
    system_prompt = _read_prompt(params["prompt_dir"])
    agent_run = vault.create_agent_run(
        agent_name=_agent_name(params["agent_name"], envelope),
        campaign_id=campaign_id,
        function_id=params["function_id"],
        status="running",
        input_payload={"channel": "linkedin", "review_kind": review_kind},
    )
    user_content = json.dumps(
        {"draft_text": draft_text, "client_references": [], "channel": "linkedin"}
    )
    response, cost = _complete_and_meter(
        gateway,
        vault,
        model="claude-sonnet",
        system_prompt=system_prompt,
        user_content=user_content,
        agent_run_id=agent_run["id"],
        content_class="public_source_content",
    )
    verdict = _parse_json_content(response["content"])
    violations = list(verdict.get("violations") or [])
    uncleared = permission_check_module.find_uncleared_references([])
    if uncleared and permission_check_module.VIOLATION_CODE not in violations:
        violations.append(permission_check_module.VIOLATION_CODE)
    violations, dropped = brand_rules.reconcile_violations(violations, draft_text)
    if dropped:
        log_event(
            logger,
            logging.WARNING,
            "qa_review_false_positive_dropped",
            review_kind=review_kind,
            dropped_violations=dropped,
        )
    vault.update_agent_run(
        agent_run["id"],
        status="succeeded" if not violations else "failed",
        output_payload={"pass": not violations, "violations": violations},
        completed_at=_now_iso(),
    )
    return violations, agent_run["id"]


def _run_qa_retry_loop(
    task_id: str,
    envelope: TaskEnvelope,
    db: Any,
    *,
    review_kind: str,
    draft_task: dict[str, Any],
    initial_violations: list[str],
    initial_draft_text: str,
    permission_check_module: Any,
) -> None:
    """Owns up to MAX_QA_RETRY_ATTEMPTS regenerate-and-recheck attempts
    for ONE draft, on behalf of BOTH per-draft QA review tasks (brand_
    steward AND fact_check) at once -- never just the review kind that
    happened to detect the first violation. This function only ever runs
    while holding the draft's advisory lock (see db.try_advisory_lock and
    this module's _single_draft_qa_review caller), which is what makes it
    safe to be the sole place that regenerates content or finalizes
    either sibling task's terminal state -- see
    claude_qa-block-retry-investigation-2026-08-11.md for the two-
    independent-task race this avoids.

    Every attempt re-runs BOTH review kinds against the newly regenerated
    draft, not just the one this loop happened to be entered for -- a fix
    aimed at a fact_check violation could accidentally introduce a brand_
    steward one, and vice versa; only a joint pass on both counts as
    success. Caller (_single_draft_qa_review) has already confirmed the
    draft's task_type has a regeneration recipe (_DRAFT_REGEN_PARAMS) and
    that none of the initial violations are NEVER_RETRYABLE."""
    draft_task_id = draft_task["task_id"]
    draft_task_type = draft_task.get("task_type")
    regen_params = _DRAFT_REGEN_PARAMS[draft_task_type]
    other_review_kind = "fact_check" if review_kind == "brand_steward" else "brand_steward"

    siblings = db.find_dependent_tasks(draft_task_id)
    other_task_type = _QA_REVIEW_PARAMS[other_review_kind]["task_type"]
    sibling_row = next((row for row in siblings if row["task_type"] == other_task_type), None)

    task_ids_by_kind = {review_kind: task_id}
    if sibling_row is not None:
        task_ids_by_kind[other_review_kind] = sibling_row["task_id"]

    never_retryable = {permission_check_module.VIOLATION_CODE}

    with build_vault_client() as vault, build_gateway_client() as gateway:
        lineage = resolve_lineage_result(draft_task_id, db)
        if lineage is None:
            log_event(
                logger,
                logging.WARNING,
                "qa_retry_loop_no_brief_ancestor",
                draft_task_id=draft_task_id,
            )
            _finalize_qa_failure(
                db,
                task_id,
                review_kind=review_kind,
                violations=initial_violations,
                draft_task=draft_task,
                draft_text=initial_draft_text,
            )
            return
        _ancestor_task, ancestor_ref = lineage
        brief_id = ancestor_ref.get("brief_id")
        brief_body = vault.get_brief(brief_id).get("body") if brief_id else None
        pillar = ancestor_ref.get("pillar")
        vertical = ancestor_ref.get("vertical")
        audience_note = ancestor_ref.get("audience_note")
        campaign_id = vault.get_or_create_campaign(
            _campaign_name(envelope), function_id=regen_params["function_id"]
        )

        current_draft_text = initial_draft_text
        current_violations = {"brand_steward": [], "fact_check": []}
        current_violations[review_kind] = initial_violations
        hollowed = False
        last_review_agent_run_ids: dict[str, str] = {}

        attempt = 0
        for attempt in range(1, MAX_QA_RETRY_ATTEMPTS + 1):
            revision_targets = sorted(
                set(current_violations["brand_steward"]) | set(current_violations["fact_check"])
            )
            if never_retryable & set(revision_targets):
                break  # a NEVER_RETRYABLE code showed up -- stop, fall to the escalation path below

            revised_text, regen_agent_run = _regenerate_draft_content(
                vault=vault,
                gateway=gateway,
                envelope=envelope,
                campaign_id=campaign_id,
                function_id=regen_params["function_id"],
                prompt_dir=regen_params["prompt_dir"],
                agent_name=regen_params["agent_name"],
                render_draft_text=regen_params["render_draft_text"],
                max_tokens=regen_params["max_tokens"],
                brief_body=brief_body,
                pillar=pillar,
                vertical=vertical,
                audience_note=audience_note,
                previous_draft_text=current_draft_text,
                violations=revision_targets,
                attempt=attempt,
            )
            if _looks_hollowed(initial_draft_text, revised_text):
                hollowed = True

            asset = vault.create_asset(
                asset_type=regen_params["asset_type"],
                agent_run_id=regen_agent_run["id"],
                campaign_id=campaign_id,
                function_id=regen_params["function_id"],
                content_bytes=revised_text.encode("utf-8"),
                approval_state="draft",
            )
            db.set_result_ref(
                draft_task_id,
                {
                    "vault_asset_id": asset["id"],
                    "content_hash": asset["content_hash"],
                    "agent_run_id": regen_agent_run["id"],
                    "campaign_id": campaign_id,
                    "pillar": pillar,
                    "retry_attempt": attempt,
                },
            )
            current_draft_text = revised_text

            bs_violations, bs_agent_run_id = _run_single_qa_check(
                vault=vault,
                gateway=gateway,
                envelope=envelope,
                campaign_id=campaign_id,
                draft_text=current_draft_text,
                review_kind="brand_steward",
                permission_check_module=permission_check_module,
            )
            fc_violations, fc_agent_run_id = _run_single_qa_check(
                vault=vault,
                gateway=gateway,
                envelope=envelope,
                campaign_id=campaign_id,
                draft_text=current_draft_text,
                review_kind="fact_check",
                permission_check_module=permission_check_module,
            )
            current_violations = {"brand_steward": bs_violations, "fact_check": fc_violations}
            last_review_agent_run_ids = {
                "brand_steward": bs_agent_run_id,
                "fact_check": fc_agent_run_id,
            }

            log_event(
                logger,
                logging.INFO,
                "qa_review_retry_attempt",
                draft_task_id=draft_task_id,
                attempt=attempt,
                brand_steward_violations=bs_violations,
                fact_check_violations=fc_violations,
                hollowed=hollowed,
            )

            if not bs_violations and not fc_violations:
                for kind, tid in task_ids_by_kind.items():
                    db.set_result_ref(
                        tid,
                        {
                            "pass": True,
                            "vault_asset_id": asset["id"],
                            "content_hash": asset["content_hash"],
                            "draft_task_id": draft_task_id,
                            "draft_task_type": draft_task_type,
                            "agent_run_id": last_review_agent_run_ids[kind],
                            "campaign_id": campaign_id,
                            "retry_attempts": attempt,
                        },
                    )
                    db.transition(tid, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
                    db.advance_dependents(tid)
                log_event(
                    logger,
                    logging.INFO,
                    "qa_review_retry_succeeded",
                    draft_task_id=draft_task_id,
                    attempts=attempt,
                )
                return

        # Exhausted MAX_QA_RETRY_ATTEMPTS, or a NEVER_RETRYABLE violation
        # appeared mid-loop -- escalate instead of silently dropping this
        # draft. Both sibling tasks get finalized FAILED/QA_BLOCKED (same
        # terminal outcome the pre-retry-loop single-shot path used) and
        # ONE Teams card carries the full track-changes context.
        final_violations = sorted(
            set(current_violations["brand_steward"]) | set(current_violations["fact_check"])
        )
        for kind, tid in task_ids_by_kind.items():
            db.set_result_ref(
                tid,
                {
                    "pass": False,
                    "violations": current_violations[kind],
                    "draft_task_id": draft_task_id,
                    "draft_task_type": draft_task_type,
                    "campaign_id": campaign_id,
                    "retry_attempts": attempt,
                },
            )
            db.transition(tid, TaskStateEnum.FAILED, TransitionReason.QA_BLOCKED)

        initial_display_text = _teams_display_text(initial_draft_text)
        current_display_text = _teams_display_text(current_draft_text)
        diff_text = "\n".join(
            difflib.unified_diff(
                initial_display_text.splitlines(),
                current_display_text.splitlines(),
                fromfile="original",
                tofile=f"after attempt {attempt}",
                lineterm="",
            )
        )
        from orchestrator import teams_notify

        teams_notify.notify_retry_exhausted(
            task_id=task_id,
            draft_task_id=draft_task_id,
            channel="linkedin",
            violations=final_violations,
            original_excerpt=initial_display_text[:280],
            revised_excerpt=current_display_text[:280],
            diff_text=diff_text[:3500],
            attempts=attempt,
            hollowed=hollowed,
        )
        log_event(
            logger,
            logging.INFO,
            "qa_review_retry_exhausted",
            draft_task_id=draft_task_id,
            violations=final_violations,
            hollowed=hollowed,
            attempts=attempt,
        )


# F-QA-CHANNEL-HARDCODED: every one of the six Wednesday drafts was
# reviewed as `channel: "linkedin"`, a literal, including the newsletter
# (email) and the case study (a web/deck asset). Function 02's checks 4
# and 5 only branch on "internal-brief" today, so this mislabel changed
# no verdict -- but it is recorded on the agent_run as the evidence of
# what was reviewed, and the moment any channel-specific rule is added it
# would be applied to the wrong asset. The review record should say what
# was actually reviewed.
DRAFT_REVIEW_CHANNELS = {
    "draft-insight-to-story": "linkedin",
    "draft-executive-ghostwrite": "linkedin",
    "draft-carousel-post": "linkedin",
    "draft-newsletter": "email",
    # Function 47's own prompt.md sets a human-initiated cadence and the
    # loop excludes it from Friday's auto-schedule: a case study is a
    # web/deck asset, never a social post.
    "draft-case-study": "web",
    # Function 52 produces linkedin_post / x_post / email_teaser
    # derivatives in one asset, so no single channel is truthful. The
    # social shape is the majority and the strictest common denominator
    # of the three, and both branch identically under function 02 today.
    "draft-content-repurpose": "linkedin",
}


def _review_channel(draft_task_type: str | None) -> str:
    return DRAFT_REVIEW_CHANNELS.get(draft_task_type or "", "linkedin")


def _reviewable_draft_text(draft_text: str) -> str:
    """The copy a reviewer should actually judge.

    Strips _render_carousel's appended Canva bulk-create CSV -- machine
    columns (slide_number/headline/subhead/image_ref/brand_template_id),
    not prose. Nothing is lost to review by dropping it: every cell in
    that manifest is generated from the slide headlines and subheads that
    remain in the text above it, so the same words are still checked,
    once. Left as-is for every other draft type (the marker is absent and
    the text passes through unchanged).

    Same reasoning that already applies to the Teams excerpt -- see
    _teams_display_text, whose incident note records what raw CSV rows
    look like to a human reader."""
    return _teams_display_text(draft_text)


def _resolve_verdict(
    function_id: str,
    verdict: dict[str, Any],
) -> tuple[bool, list[str]]:
    """Reads a QA function's verdict as the contract defines it.

    F-QA-VERDICT-FAIL-OPEN. Both review paths did
    `violations = list(verdict.get("violations") or [])` and then
    `passed = not violations`, never reading the `pass` field at all,
    while neither function 02 nor function 48 had its output validated
    anywhere -- the only stage in the pipeline with no contract
    enforcement on either side, and the one that decides what may be
    published. Two responses therefore passed content through unreviewed:

      * `{}` -- every required field missing -- scored zero violations
        and published.
      * `{"pass": false, "violations": [], "notes": "..."}` -- an explicit
        refusal with the reason in prose rather than as a code -- was
        overridden into a pass.

    Validating the output makes the first impossible (all three fields
    are required). This returns the model's own declared verdict
    alongside its codes so the caller can hold them to the schema's own
    rule -- "pass is true only when violations is empty" -- instead of
    re-deriving one from the other.
    """
    _validate_function_output(function_id, verdict)
    return bool(verdict.get("pass")), list(verdict.get("violations") or [])


# Recorded on the result_ref when a QA function declares a failure but
# names no violation code. Not one of function 02's or 48's own enum
# codes -- it is the orchestrator's account of a self-inconsistent
# verdict, and naming it separately keeps it out of the retry loop's
# recipe matching and legible to whoever reads the blocked card.
QA_VERDICT_UNSPECIFIED_FAILURE = "verdict-declared-failure-without-code"


def _single_draft_qa_review(
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
    qa_review_fact_check_handler. Reviews exactly ONE Wednesday draft --
    this task's own single ancestor, resolved via resolve_lineage_result
    -- so this task's terminal state (COMPLETED/FAILED) reflects only
    that one draft's outcome, never a sibling's. On pass, forwards
    vault_asset_id/content_hash into this task's own result_ref (same
    pattern as qa_review_handler) so schedule_social_buffer_handler /
    publish_newsletter_handler can resolve them via a plain
    resolve_lineage_result walk without needing to know about drafts at
    all."""
    permission_check = load_permission_check()
    lineage = resolve_lineage_result(task_id, db)
    if lineage is None:
        raise DispatchError(f"{task_name}: no ancestor draft task carries a result_ref to review")
    draft_task, draft_ref = lineage
    vault_asset_id = draft_ref.get("vault_asset_id")
    draft_task_type = draft_task.get("task_type")

    system_prompt = _read_prompt(prompt_dir)

    with build_vault_client() as vault:
        campaign_id = vault.get_or_create_campaign(
            _campaign_name(envelope), function_id=function_id
        )

        if not vault_asset_id and draft_ref.get("status"):
            # The draft was deliberately not attempted (_complete_undrafted
            # -- no executive configured, no cleared engagement, no proof
            # points this week). There is nothing to review and nothing
            # went wrong, so this reviews clean rather than reporting a QA
            # violation every week for a gap that is already logged and
            # already visible on the draft task's own result_ref. Crying
            # wolf here would train a reader to ignore a real QA_BLOCKED.
            #
            # `pass` is deliberately False-y for publication purposes:
            # schedule/publish resolve a vault_asset_id through this
            # result_ref and find none, so nothing can reach Buffer or
            # email on the strength of a skipped draft.
            db.set_result_ref(
                task_id,
                {
                    "status": draft_ref["status"],
                    "reviewed": False,
                    "reason": draft_ref.get("reason"),
                    "draft_task_id": draft_task["task_id"],
                    "draft_task_type": draft_task_type,
                    "campaign_id": campaign_id,
                },
            )
            db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
            log_event(
                logger,
                logging.INFO,
                "qa_review_skipped_undrafted",
                task_id=task_id,
                review_kind=review_kind,
                draft_task_id=draft_task["task_id"],
                status=draft_ref["status"],
            )
            db.advance_dependents(task_id)
            return

        if not vault_asset_id:
            # The draft dependency completed upstream but never produced a
            # reviewable asset (e.g. it was dead-lettered before writing
            # one) -- treat as a violation rather than silently skipping,
            # since QA_BLOCKED intentionally errs toward blocking. Only
            # reached when the draft left no `status` explaining itself,
            # i.e. an asset genuinely went missing.
            db.set_result_ref(
                task_id,
                {
                    "pass": False,
                    "violations": ["no_reviewable_asset"],
                    "draft_task_id": draft_task["task_id"],
                    "draft_task_type": draft_task_type,
                    "campaign_id": campaign_id,
                },
            )
            db.transition(task_id, TaskStateEnum.FAILED, TransitionReason.QA_BLOCKED)
            log_event(
                logger,
                logging.INFO,
                "qa_review_blocked",
                task_id=task_id,
                review_kind=review_kind,
                draft_task_id=draft_task["task_id"],
                violations=["no_reviewable_asset"],
            )
            return

        asset = vault.get_asset(vault_asset_id)
        draft_text = _reviewable_draft_text(
            base64.b64decode(asset["content_base64"]).decode("utf-8")
        )
        channel = _review_channel(draft_task_type)

        payload: dict[str, Any] = {
            "draft_text": draft_text,
            # The drafting functions all take an optional client_reference
            # and none is ever populated (nothing in the register is
            # CLEARED), so the caller has no names to declare -- the empty
            # list is honest here. What it is NOT is a clearance check;
            # see the find_uncleared_in_text call on the verdict below.
            "client_references": [],
            "channel": channel,
        }
        if review_kind == "fact_check":
            # F-FACT-CHECK-BLIND. Function 48 is asked to confirm "every
            # proof point in every Wednesday draft traces to a cited
            # source" and its own prompt explains the compromise it was
            # written under: "Because you receive only the draft text (not
            # the original research brief's {claim, source} pairs from
            # function 41), you verify every specific, checkable claim
            # against the three closed lists below." Those lists are a
            # snapshot of positioning.md, so any claim sourced from this
            # week's market scan was fabricated by definition -- the
            # better processes 1-4 worked, the more Thursday blocked.
            #
            # #119 and the drafting-contract change carried 41's
            # structured {claim, source} pairs to the drafts; this carries
            # them one hop further, to the check whose stated criterion
            # needs them. Pieter's sign-off, 1 Sep 2026, as function 48's
            # own prompt header requires before its scope moves.
            payload["proof_points"] = draft_ref.get("proof_points") or []
        _validate_function_input(function_id, payload)

        agent_run = vault.create_agent_run(
            agent_name=_agent_name(agent_name, envelope),
            campaign_id=campaign_id,
            function_id=function_id,
            status="running",
            input_payload={"channel": channel, "review_kind": review_kind},
        )
        user_content = json.dumps(payload)
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
                )
            set_span_attribute(span, "cost", cost)

        verdict = _parse_json_content(response["content"])
        declared_pass, violations = _resolve_verdict(function_id, verdict)
        # F-CLEARANCE-CHECK-DEAD: this called find_uncleared_references([])
        # -- a literal empty list, which cannot return anything however the
        # register is configured. On all six Wednesday drafts the only
        # deterministic, non-model clearance check did nothing at all,
        # leaving default-deny client naming resting entirely on the
        # model's own reading of check 1. It now reads the draft.
        uncleared = permission_check.find_uncleared_in_text(draft_text)
        if uncleared and permission_check.VIOLATION_CODE not in violations:
            violations.append(permission_check.VIOLATION_CODE)
            log_event(
                logger,
                logging.WARNING,
                "qa_uncleared_client_reference_found_in_draft",
                task_id=task_id,
                draft_task_id=draft_task["task_id"],
                names=[clearance.name for clearance in uncleared],
            )

        violations, dropped_violations = brand_rules.reconcile_violations(violations, draft_text)
        if dropped_violations:
            log_event(
                logger,
                logging.WARNING,
                "qa_review_false_positive_dropped",
                task_id=task_id,
                draft_task_id=draft_task["task_id"],
                review_kind=review_kind,
                dropped_violations=dropped_violations,
            )

        passed = not violations
        if passed and not declared_pass and not dropped_violations:
            # The model refused the draft but named no code -- the schema
            # forbids the combination, and nothing was reconciled away, so
            # this is a refusal whose reason lives only in `notes`. Err
            # toward blocking, as every other branch of this gate does,
            # and keep the model's own words: they are the only account of
            # why. (When reconcile_violations DID drop something, an empty
            # list is the expected, correct result of overriding a known
            # false positive -- that is not this case.)
            violations = [QA_VERDICT_UNSPECIFIED_FAILURE]
            passed = False
            log_event(
                logger,
                logging.WARNING,
                "qa_verdict_failed_without_violation_code",
                task_id=task_id,
                draft_task_id=draft_task["task_id"],
                review_kind=review_kind,
                notes=str(verdict.get("notes", ""))[:200],
            )

        vault.update_agent_run(
            agent_run["id"],
            status="succeeded" if passed else "failed",
            output_payload={"pass": passed, "violations": violations},
            completed_at=_now_iso(),
        )

    if not passed:
        # F-QA-RETRY-LOOP (11 Aug 2026): before falling back to the
        # pre-existing single-shot failure outcome, try to make this
        # violation go away with a bounded, automated retry loop --
        # unless it's the one violation class that must never be retried
        # (a regeneration attempt could itself fabricate a client
        # reference), or this draft's task_type has no regeneration
        # recipe yet (draft-content-repurpose -- see _DRAFT_REGEN_PARAMS).
        never_retryable = {permission_check.VIOLATION_CODE}
        regen_available = draft_task_type in _DRAFT_REGEN_PARAMS

        if not regen_available:
            log_event(
                logger,
                logging.INFO,
                "qa_retry_loop_not_available_for_draft_type",
                task_id=task_id,
                draft_task_type=draft_task_type,
                draft_task_id=draft_task["task_id"],
            )

        if (never_retryable & set(violations)) or not regen_available:
            _finalize_qa_failure(
                db,
                task_id,
                review_kind=review_kind,
                violations=violations,
                draft_task=draft_task,
                draft_text=draft_text,
                agent_run_id=agent_run["id"],
                campaign_id=campaign_id,
            )
            return  # never advance_dependents -- this draft's own Friday task must never see it

        # A draft's two Thursday review tasks (brand_steward / fact_check)
        # run independently and can both hit a violation at nearly the
        # same moment -- an advisory lock keyed on the draft's own
        # task_id makes whichever one gets here first the sole retry-loop
        # owner (see db.try_advisory_lock's docstring and
        # claude_qa-block-retry-investigation-2026-08-11.md for the race
        # this avoids). The one that loses the race does NOT wait on the
        # other -- it falls straight through to the same single-shot
        # outcome this task always had before this feature existed, so a
        # draft is never left hanging on an in-handler blocking wait. If
        # the owner later succeeds or exhausts its retries, it explicitly
        # re-finalizes BOTH sibling tasks (including this one) with the
        # final outcome -- see _run_qa_retry_loop -- which may mean this
        # task's own "needs edit" card here turns out to be superseded a
        # few seconds later by the owner's resolution. Documented,
        # accepted tradeoff: a possible duplicate/stale-looking Teams
        # notification is far cheaper than a stuck task handler.
        lock_key = db.advisory_lock_key_for(draft_task["task_id"])
        lock_conn = db.try_advisory_lock(lock_key)
        if lock_conn is None:
            log_event(
                logger,
                logging.INFO,
                "qa_retry_loop_deferred_to_sibling",
                task_id=task_id,
                review_kind=review_kind,
                draft_task_id=draft_task["task_id"],
            )
            _finalize_qa_failure(
                db,
                task_id,
                review_kind=review_kind,
                violations=violations,
                draft_task=draft_task,
                draft_text=draft_text,
                agent_run_id=agent_run["id"],
                campaign_id=campaign_id,
            )
            return

        try:
            _run_qa_retry_loop(
                task_id,
                envelope,
                db,
                review_kind=review_kind,
                draft_task=draft_task,
                initial_violations=violations,
                initial_draft_text=draft_text,
                permission_check_module=permission_check,
            )
        finally:
            db.release_advisory_lock(lock_conn)
        return  # never advance_dependents here -- _run_qa_retry_loop already did, on success

    passed_ref: dict[str, Any] = {
        "pass": True,
        "vault_asset_id": vault_asset_id,
        "content_hash": asset.get("content_hash"),
        "draft_task_id": draft_task["task_id"],
        "draft_task_type": draft_task_type,
        "review_kind": review_kind,
        "agent_run_id": agent_run["id"],
        "campaign_id": campaign_id,
    }
    # Friday's approval card is built from whichever ONE of this draft's
    # two Thursday gates resolve_lineage_result happens to stop at, so the
    # pillar, campaign and proof points have to survive this hop or the
    # card has nothing to describe. Same reason the QA gate carries them
    # from the brief to the drafts (F-BRIEF-FIELDS-DROPPED-BY-QA).
    passed_ref.update(_carried_brief_fields(draft_ref))
    db.set_result_ref(task_id, passed_ref)
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)

def qa_review_brand_steward_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    _single_draft_qa_review(
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
    _single_draft_qa_review(
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
# REAL gate-check (mirroring request_approval_handler exactly). Neither
# ever calls Buffer or sends an email itself -- see request_approval_
# handler's own docstring for why, and this module's docstring for
# publish-newsletter's specific caveat.
#
# ROUND 34 (docs/content-learnings.md): both are now per-draft tasks --
# one friday-schedule-social-buffer-* per eligible draft type, gated on
# that ONE draft's own 2 Thursday review tasks via resolve_lineage_result,
# same pattern as request_approval_handler. case-study deliberately has
# no friday-schedule-social-buffer-* task at all -- see
# draft_case_study_handler's docstring and function 47's own prompt.md.
# There is no more batch/weekly-cap concept at this granularity (4 draft
# types x 1 post each is already well under the old buffer_weekly_post_
# cap=8) -- each task requests exactly one gate-check for its own draft.
# ---------------------------------------------------------------------

def _complete_nothing_to_publish(
    task_id: str, db: Any, *, task_name: str, ancestor_ref: dict[str, Any]
) -> bool:
    """Friday's counterpart to _single_draft_qa_review's undrafted branch.

    A draft that was deliberately never written (no executive configured,
    no cleared engagement, no proof points this week) reaches Friday with
    a QA-gate result_ref carrying a `status` and no content_hash. Without
    this, schedule-social-buffer and publish-newsletter dead-letter on
    that missing hash every single week -- a permanent red mark standing
    for a gap that is already recorded, three tasks upstream, on the
    drafting task's own result_ref.

    Deliberately keyed on the explicit status marker and nothing else: a
    QA gate that passed a real draft but somehow carries no content_hash
    is still the dead letter it has always been."""
    status = ancestor_ref.get("status")
    if not status or ancestor_ref.get("content_hash"):
        return False
    log_event(
        logger,
        logging.INFO,
        "nothing_to_publish",
        task_id=task_id,
        task_name=task_name,
        status=status,
        draft_task_type=ancestor_ref.get("draft_task_type"),
    )
    db.set_result_ref(
        task_id,
        {
            "status": status,
            "published": False,
            "reason": ancestor_ref.get("reason"),
            "draft_task_type": ancestor_ref.get("draft_task_type"),
        },
    )
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)
    return True


def schedule_social_buffer_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    lineage = resolve_lineage_result(task_id, db)
    if lineage is None:
        raise DispatchError("schedule-social-buffer: no QA-gate ancestor carries a result_ref")
    _ancestor_task, ancestor_ref = lineage
    if _complete_nothing_to_publish(
        task_id, db, task_name="schedule-social-buffer", ancestor_ref=ancestor_ref
    ):
        return
    content_hash = ancestor_ref.get("content_hash")
    if not content_hash:
        raise DispatchError(
            "schedule-social-buffer: QA-gate ancestor result_ref carries no content_hash"
        )
    draft_task_type = ancestor_ref.get("draft_task_type")
    draft_task_id = ancestor_ref.get("draft_task_id")
    # ROUND 34 (10 Aug 2026): gate_decisions.agent_run_id is a NOT NULL FK
    # to agent_runs (contracts/vault-schema/schema.sql) -- envelope.
    # agent_run_id is a synthetic uuid5 the worker computes for tracing
    # only and no handler ever inserts into agent_runs, so it always 500s
    # with ForeignKeyViolation on a real gate-check call. Use the Thursday
    # QA-gate ancestor's own agent_run_id instead -- a real row
    # _single_draft_qa_review already created via vault.create_agent_run.
    # See request_approval_handler's docstring for the full incident.
    approving_agent_run_id = ancestor_ref.get("agent_run_id")
    if not approving_agent_run_id:
        raise DispatchError(
            "schedule-social-buffer: QA-gate ancestor result_ref carries no agent_run_id"
        )

    with build_vault_client() as vault:
        card = _approval_card_fields(task_id, db, vault, ancestor_ref)

    with build_gatekeeper_client() as gatekeeper:
        with emit_task_span(
            "schedule-social-buffer",
            function_id=REAL_PUBLISH_FUNCTION_ID,
            task_ref=task_id,
            model="none",
            cost=0.0,
            run_id=str(envelope.campaign_id),
        ):
            decision = gatekeeper.gate_check(
                agent_run_id=str(approving_agent_run_id),
                function_id=REAL_PUBLISH_FUNCTION_ID,
                action_class=REAL_PUBLISH_ACTION_CLASS,
                content_hash=content_hash,
                preview_title=card["preview_title"],
                preview_reference=f"weekly-content-loop://{task_id}",
                evidence_summary=card["evidence_summary"],
                subject=card["subject"],
            )

    db.set_result_ref(
        task_id,
        {
            "draft_task_id": draft_task_id,
            "draft_task_type": draft_task_type,
            "decision_id": decision.get("decision_id"),
            "outcome": decision.get("outcome"),
            "approval_id": decision.get("approval_id"),
            "content_hash": content_hash,
            # Process 7. The publish step runs on a separate loop, long
            # after this run has ended, and resolves nothing by lineage --
            # it finds this row by query. Everything it needs to ask "was
            # this approved, and what exactly do I publish" therefore has
            # to be ON this row: the identity the approval was raised
            # under, the policy key it was raised against, the subject the
            # gate token will carry, and the asset whose bytes are bound
            # to content_hash above.
            "agent_run_id": str(approving_agent_run_id),
            "function_id": REAL_PUBLISH_FUNCTION_ID,
            "subject": card["subject"],
            "vault_asset_id": ancestor_ref.get("vault_asset_id"),
            # Process 8. The slug the published links actually carry, and
            # the campaign it belongs to -- the pair the publish sweep
            # registers in analytics.utm_campaign_map so the nightly
            # ingest can attribute metrics to this week's content instead
            # of quarantining them.
            "campaign": ancestor_ref.get("campaign"),
            "campaign_id": ancestor_ref.get("campaign_id"),
        },
    )
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)

def publish_newsletter_handler(task_id: str, envelope: TaskEnvelope, db: Any) -> None:
    """Requests approval only -- see module docstring: no real email will
    send once approved until Publisher's Graph integration is wired with
    a real Entra ID app registration Pieter still needs to create.

    ROUND 34: gated on draft-newsletter's own 2 Thursday review tasks only
    (not all 12), resolved via resolve_lineage_result exactly like
    schedule-social-buffer -- no more searching sibling drafts for "the
    newsletter one", since this task's own dependency graph already
    guarantees its single ancestor IS the newsletter draft."""
    lineage = resolve_lineage_result(task_id, db)
    if lineage is None:
        raise DispatchError("publish-newsletter: no QA-gate ancestor carries a result_ref")
    _ancestor_task, ancestor_ref = lineage
    if _complete_nothing_to_publish(
        task_id, db, task_name="publish-newsletter", ancestor_ref=ancestor_ref
    ):
        return
    content_hash = ancestor_ref.get("content_hash")
    if not content_hash:
        raise DispatchError(
            "publish-newsletter: QA-gate ancestor result_ref carries no content_hash"
        )
    draft_task_id = ancestor_ref.get("draft_task_id")
    # ROUND 34 (10 Aug 2026): see schedule_social_buffer_handler's comment
    # / request_approval_handler's docstring -- envelope.agent_run_id is
    # never a real agent_runs row, so gate-check always 500s on the FK.
    approving_agent_run_id = ancestor_ref.get("agent_run_id")
    if not approving_agent_run_id:
        raise DispatchError(
            "publish-newsletter: QA-gate ancestor result_ref carries no agent_run_id"
        )

    with build_vault_client() as vault:
        # The newsletter's own caveat is preserved in front of the
        # generated title: "send NOT yet wired" is the single most
        # important thing an approver of this card needs to know, and it
        # is not derivable from the lineage.
        card = _approval_card_fields(
            task_id, db, vault, ancestor_ref, prefix="[NEWSLETTER — send NOT yet wired]"
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
                agent_run_id=str(approving_agent_run_id),
                function_id=REAL_NEWSLETTER_FUNCTION_ID,
                action_class=REAL_PUBLISH_ACTION_CLASS,
                content_hash=content_hash,
                preview_title=card["preview_title"],
                preview_reference=f"weekly-content-loop://{draft_task_id or task_id}",
                evidence_summary=card["evidence_summary"],
                subject=card["subject"],
            )

    db.set_result_ref(
        task_id,
        {
            "decision_id": decision.get("decision_id"),
            "outcome": decision.get("outcome"),
            "approval_id": decision.get("approval_id"),
            "content_hash": content_hash,
            # Same reasoning as schedule-social-buffer above. Note this
            # row is found by the publish loop and then declined: there is
            # no ESP send path (see publish_approved_assets_handler).
            "agent_run_id": str(approving_agent_run_id),
            "function_id": REAL_NEWSLETTER_FUNCTION_ID,
            "subject": card["subject"],
            "vault_asset_id": ancestor_ref.get("vault_asset_id"),
            "campaign": ancestor_ref.get("campaign"),
            "campaign_id": ancestor_ref.get("campaign_id"),
            "draft_task_id": draft_task_id,
            "draft_task_type": ancestor_ref.get("draft_task_type"),
        },
    )
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)

# ---------------------------------------------------------------------
# Dispatch table + legacy pass-through fallback (plan step 6; AC-01, AC-02)
# ---------------------------------------------------------------------

DISPATCH_TABLE: dict[str, Any] = {
    # The eleven S10 fan-out scanners, registered from one factory --
    # see SCANNER_TASKS and _make_scanner_handler above.
    **SCANNER_HANDLERS,
    "ingest-signals": ingest_signals_handler,
    "propose-sources": propose_sources_handler,
    "probe-sources": probe_sources_handler,
    "publish-approved-assets": publish_approved_assets_handler,
    "report-month-end": report_month_end_handler,
    "dedupe-signal-cards": dedupe_signal_cards_handler,
    "competitive-response-strategize": competitive_response_strategize_handler,
    "morning-brief-rollup": morning_brief_rollup_handler,
    "executive-brief-rollup": executive_brief_rollup_handler,
    "publish-brief": publish_brief_handler,
    "score-signals": score_signals_handler,
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

# F-DUPLICATE-TERMINAL-REQUEUE: every state a task can NEVER leave once
# reached -- _PERMANENTLY_BLOCKED_STATES (what a *dependency* can be stuck
# in forever) plus COMPLETED (the successful case, only relevant when
# checking THIS task's own state, not a dependency's -- see
# TaskAlreadyTerminalError's docstring).
_TERMINAL_STATES = _PERMANENTLY_BLOCKED_STATES | {TaskStateEnum.COMPLETED.value}

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
        if current is not None and current.get("state") in _TERMINAL_STATES:
            raise TaskAlreadyTerminalError(
                f"task {task_id} ({envelope.task_type}) already reached a "
                f"terminal state ({current['state']}); this message is a "
                "duplicate/redelivery of one already handled",
                current_state=current["state"],
            )
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
