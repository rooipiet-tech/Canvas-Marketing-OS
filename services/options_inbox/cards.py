from __future__ import annotations

import json
import pathlib
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import yaml

try:
    import jsonschema
except ImportError:  # pragma: no cover
    jsonschema = None

HERE = pathlib.Path(__file__).resolve()
ROOT = HERE.parents[2]
CONTRACT = ROOT / "contracts" / "option-card.schema.json"
MATRIX = ROOT / "policies" / "autonomy-matrix.yaml"


class CardError(ValueError):
    pass


def load_matrix() -> dict[str, Any]:
    return yaml.safe_load(MATRIX.read_text(encoding="utf-8"))


def load_contract() -> dict[str, Any]:
    return json.loads(CONTRACT.read_text(encoding="utf-8"))


def _level_allows_default(matrix: dict, level: int) -> bool:
    return bool(matrix["levels"][level].get("default_on_timeout_allowed", False))


def _default_hours(matrix: dict, level: int) -> int | None:
    return matrix["levels"][level].get("default_timeout_hours")


def build_card(
    *,
    kind: str,
    level: int,
    title: str,
    decision_question: str,
    options: list[dict[str, Any]],
    recommended: str,
    evidence_refs: list[dict[str, Any]],
    produced_by: dict[str, Any],
    register_rows: list[str],
    rationale: str = "",
    context_summary: str = "",
    novel_stance: bool = False,
    lineage: dict[str, Any] | None = None,
    now: datetime | None = None,
    expires_hours: int | None = None,
    defaults_earned: bool = False,
    evidence_resolver: Callable[[dict[str, Any]], bool] | None = None,
) -> dict[str, Any]:
    """Construct an OptionCard, deriving risk tier, budget class and timeout
    behaviour from the autonomy matrix so producers cannot get them wrong.

    defaults_earned: per policies/earn-in-rules.yaml - the caller (Fn 126's ledger)
    says whether THIS function has earned default_on_timeout. Level alone never grants it.
    evidence_resolver: callable returning True iff an EvidenceRef resolves to a real
    corpus atom / vault asset / gate decision. Counters fabricated-proof-point: an
    option cannot cite evidence that does not exist.
    """
    matrix = load_matrix()
    now = now or datetime.now(UTC)
    non_negotiable = kind in matrix["non_negotiable_kinds"]
    globally_enabled = bool(matrix.get("timeouts", {}).get("enabled_globally", False))

    if non_negotiable:
        risk_tier = "non_negotiable"
        budget_class = "realtime"
        default = None
        hours = expires_hours or 72
    else:
        risk_tier = {0: "high", 1: "medium", 2: "low", 3: "low", 4: "low"}[level]
        budget_class = "digest"
        if _level_allows_default(matrix, level) and (globally_enabled or defaults_earned):
            default = recommended
            hours = expires_hours or _default_hours(matrix, level) or 48
        else:
            default = None
            hours = expires_hours or 120

    card = {
        "card_id": str(uuid.uuid4()),
        "kind": kind,
        "autonomy_level": level,
        "risk_tier": risk_tier,
        "title": title,
        "decision_question": decision_question,
        "context_summary": context_summary,
        "options": options,
        "recommended_option_id": recommended,
        "recommendation_rationale": rationale,
        "evidence_refs": evidence_refs,
        "novel_stance": novel_stance,
        "produced_by": produced_by,
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=hours)).isoformat(),
        "default_on_timeout": default,
        "budget_class": budget_class,
        "register_rows": register_rows,
    }
    if lineage:
        card["lineage"] = lineage
    validate_card(card, evidence_resolver=evidence_resolver)
    return card


def validate_card(
    card: dict[str, Any], *, evidence_resolver: Callable[[dict[str, Any]], bool] | None = None
) -> None:
    """Schema validation plus the policy invariants the schema cannot express."""
    if jsonschema is not None:
        jsonschema.validate(card, load_contract())
    matrix = load_matrix()

    if evidence_resolver is not None:
        refs = list(card["evidence_refs"]) + [
            r for o in card["options"] for r in o.get("evidence_refs", [])
        ]
        bad = [r for r in refs if r.get("source_type") != "none" and not evidence_resolver(r)]
        if bad:
            bad_refs = [b["ref"] for b in bad]
            raise CardError(
                f"unresolvable evidence refs (fabricated-proof-point guard): {bad_refs}"
            )

    ids = [o["option_id"] for o in card["options"]]
    if len(set(ids)) != len(ids):
        raise CardError("duplicate option ids")
    if card["recommended_option_id"] not in ids:
        raise CardError("recommended_option_id is not one of the options")

    if card["kind"] in matrix["non_negotiable_kinds"]:
        if card["default_on_timeout"] is not None:
            raise CardError(f"non-negotiable kind {card['kind']} may not carry default_on_timeout")
        if card["budget_class"] != "realtime":
            raise CardError("non-negotiable kinds must be realtime")
    if card["autonomy_level"] <= 1 and card["default_on_timeout"] is not None:
        raise CardError("Level 0/1 cards may never default on timeout")
    if card["default_on_timeout"] is not None and card["default_on_timeout"] not in ids:
        raise CardError("default_on_timeout must be an option id")

    for o in card["options"]:
        if not o.get("evidence_refs") and not card.get("novel_stance"):
            raise CardError(
                f"option {o['option_id']} has no evidence and card is not flagged novel_stance"
            )

    # distinctness: summaries must not be near-identical
    summaries = [o["summary"].strip().lower() for o in card["options"]]
    if len(set(summaries)) != len(summaries):
        raise CardError("options are not distinct (identical summaries)")
    axes = [o.get("distinctness_axis") for o in card["options"]]
    if len(card["options"]) == 3 and not all(axes):
        raise CardError("three-option cards must declare a distinctness_axis per option")
