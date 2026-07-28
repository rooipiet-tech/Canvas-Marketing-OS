"""The single completion orchestrator.

main.py is router-only; everything the gateway actually decides happens
here, in this order:

  0. request-shape validation       -> the parsed body is validated against
                                       the CompletionRequest JSON Schema read
                                       straight out of the frozen
                                       contracts/model-gateway/openapi.yaml
                                       (no hand-copied schema), 400
                                       INVALID_REQUEST on any violation. This
                                       is what makes the contract's
                                       `content: type: string` an enforced
                                       runtime property rather than
                                       documentation, so an unexpected shape
                                       can never reach an upstream provider
                                       under-inspected.
  1. deliberate-hint feature flag  -> 400 NOT_IMPLEMENTED while disabled
  2. routing.yaml resolution        -> (tier, provider, provider model)
  3. redaction firewall             -> 400 + gate_decisions row on a block,
                                       provider never reached
  4. task_ref idempotency window    -> one compute() per task_ref, covering
                                       budget -> provider -> metering
  5. structured JSON log line       -> emitted for every request, including
                                       the paths that never return a
                                       CompletionResponse

Step 0 and the redaction firewall's serialize-don't-skip rule are deliberate
belt-and-suspenders: validation keeps unexpected shapes out, and the firewall
still scans them if validation is ever loosened.

Adding a provider or a logical model never edits this file: routing data
plus one registry.register() call is the whole extension path.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

import budget
import caching
import config
import gate_decisions
import jsonschema
import metering
import redaction
import routing
import yaml
from providers import registry

logger = logging.getLogger("model-gateway")

DEFAULT_MAX_TOKENS = 1024
DEFAULT_TEMPERATURE = 0.7

# The frozen contract is the single source of truth for request shape: the
# schema is read out of it at runtime, never hand-duplicated here, so a
# contract change can never silently diverge from what the gateway enforces.
# The file is located through config.contracts_dir(), which honours
# CONTRACTS_DIR — the container image has no repository checkout around it.
_request_validator: Any = None


def openapi_path() -> Path:
    """Resolved location of the frozen OpenAPI contract file."""
    return config.contracts_dir() / "model-gateway" / "openapi.yaml"


def _validator() -> Any:
    """Lazily build (and cache) the CompletionRequest schema validator."""
    global _request_validator
    if _request_validator is None:
        spec = yaml.safe_load(openapi_path().read_text(encoding="utf-8"))
        schema = spec["components"]["schemas"]["CompletionRequest"]
        _request_validator = jsonschema.Draft202012Validator(schema)
    return _request_validator


def reset_validator() -> None:
    """Drop the cached validator so the next call re-reads the contract (test hook)."""
    global _request_validator
    _request_validator = None


def validate_request(payload: dict) -> str | None:
    """Return a human-readable violation message, or None if the body is valid.

    THE MESSAGE NEVER CONTAINS ANY SUBMITTED VALUE. jsonschema's own
    ``ValidationError.message`` embeds the offending instance's ``repr()``
    verbatim, and this runs at step 0 — before the redaction firewall has
    scanned anything. Echoing it back would hand a caller's personal
    information straight into a client-facing 400 body, unscanned and with no
    gate_decisions audit row, which is precisely the transfer the firewall
    exists to prevent. So the message is assembled from schema-side facts
    only: WHERE in the document the violation is (``absolute_path``), WHICH
    keyword failed (``validator``), and what the frozen contract requires
    (``validator_value``). All three come from our own contract file, never
    from the request.

    `format` keywords (agent_run_id's `format: uuid`) are deliberately not
    asserted: the frozen contract documents them, but tightening an
    annotation-only keyword into a hard rejection would be a behavioural
    contract change, not enforcement of one.
    """
    errors = sorted(_validator().iter_errors(payload), key=lambda e: list(e.absolute_path))
    if not errors:
        return None
    first = errors[0]
    location = "/".join(str(p) for p in first.absolute_path) or "(root)"
    return (
        f"request does not match the CompletionRequest contract at {location}: "
        f"expected {first.validator} constraint {first.validator_value!r} "
        f"(submitted value omitted)"
    )


class BudgetHardBreach(Exception):
    """Carries the 429 body for an exhausted per-function daily budget.

    Raised inside the compute closure so caching never stores it: a retry
    re-evaluates the budget from scratch instead of replaying a stale 429.
    """

    def __init__(self, body: dict):
        super().__init__("budget hard breach")
        self.body = body


def _error(code: str, message: str) -> dict:
    """Build a body matching the frozen Error schema."""
    return {"error": {"code": code, "message": message}}


def _log(
    payload: dict,
    *,
    routing_tier: str | None,
    cache_hit: bool | None,
    budget_state: str | None,
    redaction_outcome: str,
    status_code: int,
) -> None:
    """One structured JSON line per request — the agent-readable record."""
    logger.info(
        json.dumps(
            {
                "event": "completion",
                "agent_run_id": payload.get("agent_run_id"),
                "model": payload.get("model"),
                "task_ref": payload.get("task_ref"),
                "routing_tier": routing_tier,
                "cache_hit": cache_hit,
                "budget_state": budget_state,
                "redaction_outcome": redaction_outcome,
                "status_code": status_code,
            }
        )
    )


async def handle_completion(payload: dict, repo: Any) -> tuple[int, dict]:
    """Handle one POST /v1/completions. Returns (status_code, body)."""
    started = time.perf_counter()

    # 0. contract-shape validation, before anything reads the body's fields.
    violation = validate_request(payload)
    if violation is not None:
        _log(
            payload,
            routing_tier=None,
            cache_hit=None,
            budget_state=None,
            redaction_outcome="not_scanned",
            status_code=400,
        )
        return 400, _error("INVALID_REQUEST", violation)

    agent_run_id = payload.get("agent_run_id")
    model = payload.get("model")
    messages = payload.get("messages")
    if not model or not agent_run_id or not isinstance(messages, list) or not messages:
        body = _error(
            "INVALID_REQUEST",
            "model, messages and agent_run_id are required",
        )
        _log(
            payload,
            routing_tier=None,
            cache_hit=None,
            budget_state=None,
            redaction_outcome="not_scanned",
            status_code=400,
        )
        return 400, body

    # 1. deliberate hint — explicit, flag-gated, never silently ignored.
    if payload.get("deliberate") is True and not config.DELIBERATE_FLAG_ENABLED:
        body = _error(
            "NOT_IMPLEMENTED",
            "the 'deliberate' reasoning hint is accepted by this contract but "
            "not yet implemented; it is gated behind the DELIBERATE_FLAG_ENABLED "
            "feature flag, currently disabled",
        )
        _log(
            payload,
            routing_tier=None,
            cache_hit=None,
            budget_state=None,
            redaction_outcome="not_scanned",
            status_code=400,
        )
        return 400, body

    # 2. routing
    try:
        route = routing.resolve(str(model))
    except ValueError:
        # DR-5: the wire message is built from policy/routing.yaml's own
        # contents, never from the submitted `model`. Deliberately not
        # `str(exc)`: this runs before the redaction firewall and writes no
        # gate_decisions row, so echoing a caller-controlled string here would
        # be an unscanned, unaudited exfiltration path — exactly what DR-3
        # closed on the shape-validation path above. The submitted value is
        # still recorded server-side by _log() below.
        _log(
            payload,
            routing_tier=None,
            cache_hit=None,
            budget_state=None,
            redaction_outcome="not_scanned",
            status_code=400,
        )
        return 400, _error("UNKNOWN_MODEL", routing.unknown_model_message())

    # 3. redaction firewall — before any provider adapter call.
    #
    # `scan.matched_pattern_id` is the ONLY thing the scan reports, and
    # redaction.py guarantees it is an opaque contract-side coordinate (a
    # pattern id, or `fixture:<group>:<index>`) that never embeds the matched
    # text — see DR-4. That guarantee is what makes it safe to put in both the
    # caller-facing body and the permanent gate_decisions.reason column below.
    scan = redaction.scan_request(payload)
    if scan.blocked:
        await gate_decisions.insert_gate_decision(
            repo,
            agent_run_id=str(agent_run_id),
            decided_by=gate_decisions.REDACTION_GATE_DECIDER,
            outcome=gate_decisions.OUTCOME_REJECTED,
            reason=f"redaction pattern matched: {scan.matched_pattern_id}",
        )
        _log(
            payload,
            routing_tier=route.tier,
            cache_hit=None,
            budget_state=None,
            redaction_outcome="blocked",
            status_code=400,
        )
        body = _error(
            "REDACTION_BLOCKED",
            "request blocked by the redaction firewall before any upstream "
            f"call (pattern: {scan.matched_pattern_id})",
        )
        # Additive response-field channel for the block signal (the frozen
        # Error schema sets no additionalProperties: false). The tier was
        # already resolved before the block, and surfacing it here means an
        # agent reading only response bodies sees the same picture as one
        # reading logs — independent of how logging happens to be configured.
        body["routing_tier"] = route.tier
        body["redaction_outcome"] = "blocked"
        return 400, body

    observed: dict[str, Any] = {"budget_state": "ok", "routing_tier": route.tier}

    async def _compute() -> dict:
        resolved_tier, budget_state = await budget.check_and_apply_budget(
            repo, str(agent_run_id), route.tier
        )
        observed["budget_state"] = budget_state

        if budget_state == "hard_breach":
            gate_id = await gate_decisions.insert_gate_decision(
                repo,
                agent_run_id=str(agent_run_id),
                decided_by=gate_decisions.BUDGET_GATE_DECIDER,
                outcome=gate_decisions.OUTCOME_ESCALATED,
                reason="per-function daily budget exhausted; completion queued for review",
            )
            body = _error(
                "BUDGET_EXHAUSTED",
                "per-function daily budget exhausted; this completion has been "
                "queued for review rather than dropped",
            )
            body["queued_task_ref"] = gate_id
            body["routing_tier"] = resolved_tier
            body["budget_state"] = budget_state
            raise BudgetHardBreach(body)

        effective = route
        if resolved_tier != route.tier:
            # Soft breach: keep serving, one tier cheaper.
            effective = routing.resolve_by_tier(resolved_tier)
        observed["routing_tier"] = effective.tier

        provider = registry.get_provider(effective.provider)
        result = await provider.complete(
            provider_model=effective.provider_model,
            messages=messages,
            max_tokens=int(payload.get("max_tokens") or DEFAULT_MAX_TOKENS),
            temperature=float(
                payload.get("temperature")
                if payload.get("temperature") is not None
                else DEFAULT_TEMPERATURE
            ),
            tools=payload.get("tools"),
        )

        latency_ms = (time.perf_counter() - started) * 1000.0
        usd = metering.estimate_usd(effective.tier, result.input_tokens, result.output_tokens)
        cost_id = await metering.record_completion_costs(
            repo,
            agent_run_id=str(agent_run_id),
            provider=effective.provider,
            usd=usd,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            latency_ms=latency_ms,
        )

        return {
            "id": f"cmpl_{uuid.uuid4().hex}",
            "model": str(model),
            "content": result.content,
            "usage": {
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
            },
            "agent_run_id": str(agent_run_id),
            "cost_id": cost_id,
            # Additive observability fields (the frozen response schema sets
            # no additionalProperties: false, so these are contract-safe).
            "routing_tier": effective.tier,
            "budget_state": budget_state,
        }

    # 4. one compute() per task_ref, spanning the whole provider window.
    try:
        response, cache_hit = await caching.get_or_compute(payload.get("task_ref"), _compute)
    except BudgetHardBreach as exc:
        _log(
            payload,
            routing_tier=observed["routing_tier"],
            cache_hit=False,
            budget_state="hard_breach",
            redaction_outcome="ok",
            status_code=429,
        )
        return 429, exc.body

    response = dict(response)
    response["cache_hit"] = cache_hit
    _log(
        payload,
        routing_tier=response.get("routing_tier"),
        cache_hit=cache_hit,
        budget_state=response.get("budget_state"),
        redaction_outcome="ok",
        status_code=200,
    )
    return 200, response
