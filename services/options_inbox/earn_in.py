"""Pure evaluation logic for policies/earn-in-rules.yaml (Appendix D PR 4).

This module answers three questions against that file's promote/demote/
default_on_timeout tables, generically -- it does not know the specific
metric names in advance, so a new `_gte`/`_lte`-suffixed requirement in
the YAML needs no code change here:

  * evaluate_promotion  -- given a function's current level, action_class
    and a window of measured metrics, is it eligible for the next level?
  * evaluate_demotion   -- given live signals, which demote triggers (if
    any) fire, and what's the resulting action?
  * default_on_timeout_earned -- has this specific function earned
    default_on_timeout, independent of the file's own `enabled_globally`
    kill switch (that's a separate, simpler check the caller already
    makes -- see cards.py's `globally_enabled or defaults_earned`).

Deliberately has NO knowledge of Fn 126 (the future ledger that will call
this against real measured data, App D PR 6/7) or of any database --
callers hand it plain dicts of already-computed metrics/signals. Keeping
the threshold arithmetic here, tested once, means Fn 126's own job is
only "compute the metrics dict correctly," not "get the promotion math
right too."
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
# CONTRACTS_DIR/FUNCTIONS_DIR pattern (orchestrator/config.py, L-0062;
# see cards.py's identical note for the full incident this guards
# against) -- REPO_ROOT-relative resolution only holds in a full
# repository checkout, never inside a container that regular-installs
# this package.
_EARN_IN_RULES_DEFAULT = REPO_ROOT / "policies" / "earn-in-rules.yaml"
_earn_in_override = os.environ.get("EARN_IN_RULES_PATH", "").strip()
EARN_IN_RULES_PATH = Path(_earn_in_override) if _earn_in_override else _EARN_IN_RULES_DEFAULT

# requires keys this evaluator interprets structurally, never as a
# generic _gte/_lte/exact-match metric comparison.
_STRUCTURAL_REQUIRES_KEYS = {"window_days", "min_runs", "applies_to", "downstream_extra"}
_DOWNSTREAM_EXTRA_MIN_PUBLISHED = 20


def load_rules(path: Path = EARN_IN_RULES_PATH) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def action_class_track(action_class: str, rules: dict[str, Any]) -> str:
    """'upstream' or 'downstream'. Raises if action_class is classified in
    neither or both -- scripts/validate_autonomy_policy.py is the build-
    time guard that should have already caught this; this is the runtime
    backstop."""
    classes = rules["action_classes"]
    upstream = set(classes.get("upstream") or [])
    downstream = set(classes.get("downstream") or [])
    in_upstream, in_downstream = action_class in upstream, action_class in downstream
    if in_upstream and in_downstream:
        raise ValueError(
            f"action_class {action_class!r} is classified as both upstream and downstream"
        )
    if in_upstream:
        return "upstream"
    if in_downstream:
        return "downstream"
    raise ValueError(
        f"action_class {action_class!r} is classified as neither upstream nor downstream"
    )


@dataclass(frozen=True)
class PromotionResult:
    eligible: bool
    from_level: int
    to_level: int | None
    unmet: list[str] = field(default_factory=list)


def evaluate_promotion(
    *, current_level: int, action_class: str, metrics: dict[str, Any], rules: dict[str, Any]
) -> PromotionResult:
    """metrics carries whatever the matched rule's `requires` block asks
    for: `window_days` and `run_count` describe the observation window;
    every other key is read by stripping a `_gte`/`_lte` suffix from the
    rule's requirement name (e.g. `gate_pass_rate_gte` reads
    metrics['gate_pass_rate']); a bare key with no suffix
    (fabricated_proof_point_events, material_failures, guardrail_breaches)
    requires an exact match, per the YAML's own comment ("zero. Not
    'low'.") — these are counts, and a _lte of 0 would mean the same
    thing, but the exact-match reading is what the file's prose says."""
    rule = next((r for r in rules["promote"] if r["from"] == current_level), None)
    if rule is None:
        return PromotionResult(False, current_level, None, ["no promotion rule from this level"])

    requires = rule["requires"]
    track = action_class_track(action_class, rules)
    unmet: list[str] = []

    if requires.get("applies_to") == "upstream_only" and track != "upstream":
        return PromotionResult(
            False,
            current_level,
            None,
            [f"promotion {rule['from']}->{rule['to']} is upstream_only; this function is {track}"],
        )

    if "window_days" in requires:
        observed_days = metrics.get("window_days", 0)
        if observed_days < requires["window_days"]:
            unmet.append(f"window_days {observed_days} < required {requires['window_days']}")

    if "min_runs" in requires:
        observed_runs = metrics.get("run_count", 0)
        if observed_runs < requires["min_runs"]:
            unmet.append(f"run_count {observed_runs} < required min_runs {requires['min_runs']}")

    for key, threshold in requires.items():
        if key in _STRUCTURAL_REQUIRES_KEYS:
            continue
        if key.endswith("_gte"):
            metric_name = key[: -len("_gte")]
            value = metrics.get(metric_name)
            if value is None or value < threshold:
                unmet.append(f"{metric_name} {value} < required {threshold}")
        elif key.endswith("_lte"):
            metric_name = key[: -len("_lte")]
            value = metrics.get(metric_name)
            if value is None or value > threshold:
                unmet.append(f"{metric_name} {value} > allowed {threshold}")
        else:
            value = metrics.get(key)
            if value != threshold:
                unmet.append(f"{key} {value} != required exactly {threshold}")

    if "downstream_extra" in requires and track == "downstream":
        published = metrics.get("published_zero_correction_count", 0)
        if published < _DOWNSTREAM_EXTRA_MIN_PUBLISHED:
            unmet.append(
                f"published_zero_correction_count {published} < required "
                f"{_DOWNSTREAM_EXTRA_MIN_PUBLISHED} ({requires['downstream_extra']})"
            )

    eligible = not unmet
    return PromotionResult(eligible, current_level, rule["to"] if eligible else None, unmet)


@dataclass(frozen=True)
class DemotionResult:
    trigger: str
    action: str


def evaluate_demotion(*, signals: dict[str, Any], rules: dict[str, Any]) -> list[DemotionResult]:
    """signals carries whatever each demote rule's trigger needs, keyed by
    the trigger name with its `_gt`/`_lt` suffix stripped (e.g.
    `gate_pass_rate_lt`'s value reads signals['gate_pass_rate']), plus
    `material_failure` (bool) for the one non-thresholded trigger and
    `run_count`/`decision_count` for triggers that specify a `min_runs`/
    `min_decisions` observation floor. More than one trigger can fire in
    the same evaluation; callers apply the most severe action (pause_
    function > drop_to_level_1 > drop_one_level_and_pause_until_card >
    drop_one_level is this file's own conservative ordering, not encoded
    here since severity ordering is a policy call this module leaves to
    its caller)."""
    fired: list[DemotionResult] = []
    for rule in rules["demote"]:
        trigger = rule["trigger"]
        action = rule["action"]

        if trigger == "material_failure":
            if signals.get("material_failure"):
                fired.append(DemotionResult(trigger, action))
            continue

        threshold = rule.get("threshold")
        if threshold is None:
            continue  # a trigger shape this evaluator does not recognise -- skip, don't guess

        if trigger.endswith("_gt"):
            metric_name, exceeds = trigger[: -len("_gt")], True
        elif trigger.endswith("_lt"):
            metric_name, exceeds = trigger[: -len("_lt")], False
        else:
            continue

        min_observations = rule.get("min_runs") or rule.get("min_decisions")
        observed = signals.get("run_count") or signals.get("decision_count")
        if min_observations is not None and (observed is None or observed < min_observations):
            continue

        value = signals.get(metric_name)
        if value is None:
            continue
        if (exceeds and value > threshold) or (not exceeds and value < threshold):
            fired.append(DemotionResult(trigger, action))

    return fired


def default_on_timeout_earned(
    *, level: int, action_class: str, consecutive_promotion_windows_met: int, rules: dict[str, Any]
) -> bool:
    """Whether THIS function has earned default_on_timeout — independent
    of `default_on_timeout.enabled_globally`, which is a separate,
    simpler kill switch the caller already ORs in (cards.py's own
    `globally_enabled or defaults_earned`)."""
    enable_when = rules["default_on_timeout"]["enable_per_function_when"]
    track = action_class_track(action_class, rules)
    return (
        track == enable_when["action_class"]
        and level >= enable_when["level_gte"]
        and consecutive_promotion_windows_met >= enable_when["consecutive_promotion_windows_met"]
    )
