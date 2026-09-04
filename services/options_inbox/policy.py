from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .cards import load_matrix


@dataclass
class RoutingResult:
    sent: list[dict] = field(default_factory=list)
    auto_resolved: list[tuple[dict, str]] = field(default_factory=list)  # (card, permission_id)
    timeout_defaults: list[dict] = field(default_factory=list)
    expired_unresolved: list[dict] = field(default_factory=list)
    queued_overflow: list[dict] = field(default_factory=list)
    escalations: list[dict] = field(default_factory=list)


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def permission_matches(perm: dict[str, Any], card: dict[str, Any], context: dict[str, Any]) -> bool:
    """Deterministic predicate. `condition` is evaluated against a restricted
    namespace - no builtins - so a permission can never become code execution."""
    if perm.get("status") != "active":
        return False
    if card["kind"] not in perm["scope"]["card_kinds"]:
        return False
    if card["kind"] in perm["rule"].get("hard_exclusions", []):
        return False
    matrix = load_matrix()
    if card["kind"] in matrix["non_negotiable_kinds"]:
        return False  # belt and braces: validator also refuses such permissions
    fns = perm["scope"].get("functions")
    if fns and card["produced_by"]["function_id"] not in fns:
        return False
    ns = {"card": card, "ctx": context, "all": all, "any": any, "len": len}
    try:
        return bool(eval(perm["rule"]["condition"], {"__builtins__": {}}, ns))
    except Exception:
        return False


def route(
    pending: list[dict[str, Any]],
    permissions: list[dict[str, Any]],
    *,
    now: datetime | None = None,
    realtime_sent_today: int = 0,
    value_score: dict[str, float] | None = None,
    resurfaced: set[str] | None = None,
    context: dict[str, Any] | None = None,
) -> RoutingResult:
    """Fn 117's core. Pure function: no I/O, fully testable."""
    matrix = load_matrix()
    budget = int(matrix["approval_budget"]["cards_per_working_day"])
    now = now or datetime.now(UTC)
    value_score = value_score or {}
    resurfaced = resurfaced or set()
    context = context or {}
    result = RoutingResult()

    remaining: list[dict] = []
    for card in pending:
        # 1. standing permissions
        matched = next((p for p in permissions if permission_matches(p, card, context)), None)
        if matched and matched["rule"]["effect"] == "auto_approve_recommended":
            result.auto_resolved.append((card, matched["permission_id"]))
            continue
        # 2. timeouts
        if _parse(card["expires_at"]) <= now:
            if card["default_on_timeout"] is not None:
                result.timeout_defaults.append(card)
            elif card["card_id"] in resurfaced:
                result.expired_unresolved.append(card)
            else:
                result.escalations.append(card)  # second showing, realtime
            continue
        remaining.append(card)

    # 3. realtime escalations bypass the budget
    digest: list[dict] = []
    for card in remaining:
        if card["budget_class"] == "realtime":
            result.escalations.append(card)
        else:
            digest.append(card)

    # 4. rank and budget
    digest.sort(key=lambda c: value_score.get(c["card_id"], 0.0), reverse=True)
    slots = max(0, budget - realtime_sent_today)
    result.sent = digest[:slots]
    result.queued_overflow = digest[slots:]
    return result
