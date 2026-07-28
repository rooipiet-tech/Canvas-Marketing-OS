"""The single completion orchestrator.

main.py is router-only; everything the gateway actually decides happens
here, in this order:

  1. deliberate-hint feature flag  -> 400 NOT_IMPLEMENTED while disabled
  2. routing.yaml resolution        -> (tier, provider, provider model)
  3. redaction firewall             -> 400 + gate_decisions row on a block,
                                       provider never reached
  4. task_ref idempotency window    -> one compute() per task_ref, covering
                                       budget -> provider -> metering
  5. structured JSON log line       -> emitted for every request, including
                                       the paths that never return a
                                       CompletionResponse

Adding a provider or a logical model never edits this file: routing data
plus one registry.register() call is the whole extension path.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

import budget
import caching
import config
import gate_decisions
import metering
import redaction
import routing
from providers import registry

logger = logging.getLogger("model-gateway")

DEFAULT_MAX_TOKENS = 1024
DEFAULT_TEMPERATURE = 0.7


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
    except ValueError as exc:
        _log(
            payload,
            routing_tier=None,
            cache_hit=None,
            budget_state=None,
            redaction_outcome="not_scanned",
            status_code=400,
        )
        return 400, _error("UNKNOWN_MODEL", str(exc))

    # 3. redaction firewall — before any provider adapter call.
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
        return 400, _error(
            "REDACTION_BLOCKED",
            "request blocked by the redaction firewall before any upstream "
            f"call (pattern: {scan.matched_pattern_id})",
        )

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
