"""orchestrator/dispatch.py — the real per-task-type dispatch mechanism
(plan steps 6-13; AC-01, AC-02, AC-05, AC-06, AC-20, AC-24, AC-28, AC-30,
AC-31).

DISPATCH_TABLE maps exactly the 5 GOAL-mandated task_types to a real
handler that produces a real downstream artifact — a costs-table row
(via a real model-gateway call), a Vault signal/brief/asset, or a
gate-token request + approval-inbox card. Every task_type NOT in this
table (every already-real S10/S11 task_type, plus any genuinely
unregistered one) falls through to legacy_task_pass_through, which is
BYTE-IDENTICAL to worker.py's own pre-session unconditional stub
(RUNNING -> COMPLETED -> advance_dependents) — nothing here regresses an
already-shipped loop (AC-02).

request-approval's scope is bounded exactly as AC-01 requires: it calls
Gatekeeper's /gate-check once and completes as soon as that responds. It
NEVER polls or waits for the human decision — that arrives asynchronously
via the Gatekeeper/approval-inbox surface, entirely outside this task.

draft-content / qa-review (when invoked with params.proof_circuit) /
request-approval together form the S8 PROOF CIRCUIT (AC-30): every
Vault agent_run they create is tagged agent_name=AGENT_NAME_LOOP_PROOF,
and request-approval's gate-check request carries the PROOF_CIRCUIT_TAG
in both preview_reference and preview_title so it is unmistakable on
every surface that renders it (console's approvals.html only renders
preview_title — see request_approval_handler's docstring).
"""

from __future__ import annotations

import importlib.util
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
from telemetry_lib import set_span_attribute

from orchestrator.clients.gatekeeper_client import GatekeeperClient, resolve_gatekeeper_base_url
from orchestrator.clients.gateway_client import OrchestratorGatewayClient, resolve_gateway_base_url
from orchestrator.clients.mcp_client import MCPClient, resolve_mcp_web_base_url
from orchestrator.clients.vault_client_ext import VaultClientExt, resolve_vault_base_url
from orchestrator.logging_config import get_logger, log_event, sanitize_exception_text
from orchestrator.models import TaskEnvelope, TaskStateEnum, TransitionReason
from orchestrator.telemetry_wiring import emit_task_span

logger = get_logger("dispatch")

REPO_ROOT = Path(__file__).resolve().parents[3]
FUNCTIONS_DIR = REPO_ROOT / "functions"

FUNCTION_ID_09 = "09-market-intelligence-director"
FUNCTION_ID_42 = "42-linkedin-post-writer"
FUNCTION_ID_02 = "02-brand-steward-qa"
# Deterministic rendering only (plan step 9) -- no LLM call, so this isn't
# one of the numbered function packages under functions/.
FUNCTION_ID_BRIEF_COMPOSE = "brief.compose"

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
    backstop (C5, state_machine.record_failure) to eventually reconcile —
    the same fate as any other infra-level dispatch failure, never a
    silent COMPLETED."""


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
    module_path = FUNCTIONS_DIR / "02-brand-steward-qa" / "permission_check.py"
    spec = importlib.util.spec_from_file_location(_PERMISSION_CHECK_MODULE_NAME, module_path)
    if spec is None or spec.loader is None:
        raise DispatchError(f"cannot load permission_check.py from {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_PERMISSION_CHECK_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


def _read_prompt(function_dir_name: str) -> str:
    return (FUNCTIONS_DIR / function_dir_name / "prompt.md").read_text(encoding="utf-8")


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
    prompt asks the model to make a single bare JSON object -- strip any
    accidental markdown code fence before parsing, but never invent
    content on a parse failure."""
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError as exc:
        raise DispatchError(f"model response was not valid JSON: {exc}") from exc


def _complete_and_meter(
    gateway: OrchestratorGatewayClient,
    vault: VaultClientExt,
    *,
    model: str,
    system_prompt: str,
    user_content: str,
    agent_run_id: str,
) -> tuple[dict[str, Any], float]:
    """One completion + a best-effort read-back of its REAL metered cost
    (model-gateway's own metering.py already wrote 3 costs rows
    automatically, keyed by agent_run_id -- this just reads the usd row
    back for the span's cost attribute; a lookup failure never blocks the
    handler, it only means the span's cost stays 0.0)."""
    response = gateway.complete(
        model=model,
        system_prompt=system_prompt,
        user_content=user_content,
        agent_run_id=agent_run_id,
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
    path = FUNCTIONS_DIR / "09-market-intelligence-director" / "fetch_sources.yaml"
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
        campaign_id = vault.get_or_create_campaign(_campaign_name(envelope))
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
        user_content = _build_ingest_user_content(sources, fetched)

        with emit_task_span(
            "ingest-signals",
            function_id=FUNCTION_ID_09,
            task_ref=task_id,
            model="claude-haiku",
            run_id=str(envelope.campaign_id),
        ) as span:
            with build_gateway_client() as gateway:
                response, cost = _complete_and_meter(
                    gateway,
                    vault,
                    model="claude-haiku",
                    system_prompt=system_prompt,
                    user_content=user_content,
                    agent_run_id=agent_run["id"],
                )
            set_span_attribute(span, "cost", cost)

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
        # Cite the source by DOMAIN only, never the bare source_url --
        # this is an internal brief, not customer-facing content, and a
        # raw external citation link (a Microsoft Learn page, an SA news
        # RSS item) is neither a Canvas Intelligence CTA link nor one that
        # should ever carry Canvas's own utm_* parameters. Embedding the
        # full URL here would make function 02's qa-review judge an
        # internal citation against customer-facing link rules it was
        # never meant to satisfy.
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
        campaign_id = vault.get_or_create_campaign(_campaign_name(envelope))
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
        campaign_id = vault.get_or_create_campaign(_campaign_name(envelope))

        client_references: list[str] = []
        if ancestor_task["task_type"] == "draft-content":
            channel = "linkedin"
            asset = vault.get_asset(ancestor_ref["vault_asset_id"])
            import base64

            draft_text = base64.b64decode(asset["content_base64"]).decode("utf-8")
        else:
            channel = "web"
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
                )
            set_span_attribute(span, "cost", cost)

            verdict = _parse_json_content(response["content"])
            violations = list(verdict.get("violations") or [])

            # Deterministic, code-level enforcement of the uncleared-
            # client-block (AC-06) -- reuses function 02's OWN
            # permission_check.py by reference (AC-31), never a
            # duplicated/forked copy of its logic. Merged with (not
            # instead of) whatever the LLM verdict itself already flagged.
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
        log_event(
            logger, logging.INFO, "qa_review_blocked", task_id=task_id, violations=violations
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
        campaign_id = vault.get_or_create_campaign(_campaign_name(envelope))

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
        },
    )
    # Completes as soon as /gate-check responds -- never waits/polls on
    # the human decision (AC-01's bounded scope for this task_type).
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
}


def legacy_task_pass_through(task_id: str, task_type: str, db: Any) -> None:
    """BYTE-IDENTICAL to worker.py's pre-session unconditional stub
    (RUNNING -> COMPLETED -> advance_dependents). Used for every
    already-real S10/S11 task_type (AC-02's regression-guard requirement)
    and for any genuinely unregistered task_type -- in the latter case,
    if `task_id` has no backing task_state row (the e2e test's synthetic
    'zzz-unregistered-test-type' case), the first db.transition() call's
    own task_transitions FK constraint raises naturally; that propagates
    to worker.py's existing outer try/except (task_handling_failed
    logged), leaving no unhandled exception and no silently-COMPLETED
    task -- the message is still safely completed at transport level
    regardless (worker.py's `finally` block).
    """
    db.transition(task_id, TaskStateEnum.RUNNING, TransitionReason.DISPATCHED)
    db.transition(task_id, TaskStateEnum.COMPLETED, TransitionReason.COMPLETED)
    db.advance_dependents(task_id)
    log_event(
        logger, logging.INFO, "legacy_task_pass_through", task_id=task_id, task_type=task_type
    )


def dispatch_task(envelope: TaskEnvelope, db: Any) -> None:
    """The one entry point worker.handle_task_message calls. Routes to a
    real handler for the 5 GOAL-mandated task_types; everything else
    (already-real S10/S11 types, or a genuinely unregistered one) takes
    the legacy pass-through path unchanged from pre-session behaviour."""
    task_id = str(envelope.task_id)
    handler = DISPATCH_TABLE.get(envelope.task_type)
    if handler is None:
        legacy_task_pass_through(task_id, envelope.task_type, db)
        return
    db.transition(task_id, TaskStateEnum.RUNNING, TransitionReason.DISPATCHED)
    handler(task_id, envelope, db)
