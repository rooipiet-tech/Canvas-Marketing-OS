"""Pure earn-in evaluation logic tests (Appendix D PR 4).

Runs against the REAL policies/earn-in-rules.yaml, never a hand-rolled
fixture -- catching a drift between this file and that YAML is the
entire point (the same reasoning scripts/validate_autonomy_policy.py's
own header gives for reading the real autonomy.yaml)."""

from __future__ import annotations

import pytest
from options_inbox.earn_in import (
    action_class_track,
    default_on_timeout_earned,
    evaluate_demotion,
    evaluate_promotion,
    load_rules,
)

RULES = load_rules()


def test_real_file_classifies_every_action_class_gatekeeper_ships():
    # The exact drift this module exists to prevent: draft/configure were
    # added to autonomy.yaml without a track classification here.
    for action_class in ("publish", "draft", "configure", "analyse"):
        assert action_class_track(action_class, RULES) in ("upstream", "downstream")


def test_configure_is_downstream_capped_at_two():
    # autonomy.yaml's own comment: config.source_promotion "never level 3
    # or 4, however strong the probe evidence looks." Confirmed here by
    # RULES' own ceiling_without_card.downstream, not asserted directly.
    assert action_class_track("configure", RULES) == "downstream"
    assert RULES["defaults"]["ceiling_without_card"]["downstream"] == 2


def test_level_1_to_2_promotion_all_criteria_met():
    metrics = {
        "window_days": 30,
        "run_count": 40,
        "gate_pass_rate": 0.70,
        "recommendation_hit_rate": 0.60,
        "fabricated_proof_point_events": 0,
        "material_failures": 0,
    }
    result = evaluate_promotion(
        current_level=1, action_class="draft", metrics=metrics, rules=RULES
    )
    assert result.eligible is True
    assert result.to_level == 2
    assert result.unmet == []


def test_level_1_to_2_promotion_one_criterion_short():
    metrics = {
        "window_days": 30,
        "run_count": 40,
        "gate_pass_rate": 0.69,  # just under the 0.70 floor
        "recommendation_hit_rate": 0.60,
        "fabricated_proof_point_events": 0,
        "material_failures": 0,
    }
    result = evaluate_promotion(
        current_level=1, action_class="draft", metrics=metrics, rules=RULES
    )
    assert result.eligible is False
    assert any("gate_pass_rate" in reason for reason in result.unmet)


def test_a_single_fabricated_proof_point_event_blocks_promotion():
    # The YAML's own comment: "zero. Not 'low'. This is the dominant
    # failure mode." -- one event, everything else perfect, still blocked.
    metrics = {
        "window_days": 30,
        "run_count": 40,
        "gate_pass_rate": 0.95,
        "recommendation_hit_rate": 0.95,
        "fabricated_proof_point_events": 1,
        "material_failures": 0,
    }
    result = evaluate_promotion(
        current_level=1, action_class="draft", metrics=metrics, rules=RULES
    )
    assert result.eligible is False
    assert any("fabricated_proof_point_events" in reason for reason in result.unmet)


def test_insufficient_run_count_blocks_promotion_even_if_rates_are_perfect():
    metrics = {
        "window_days": 30,
        "run_count": 39,  # one short of the required 40
        "gate_pass_rate": 1.0,
        "recommendation_hit_rate": 1.0,
        "fabricated_proof_point_events": 0,
        "material_failures": 0,
    }
    result = evaluate_promotion(
        current_level=1, action_class="draft", metrics=metrics, rules=RULES
    )
    assert result.eligible is False
    assert any("run_count" in reason for reason in result.unmet)


def test_level_3_to_4_is_upstream_only():
    metrics = {
        "window_days": 90,
        "run_count": 150,
        "gate_pass_rate": 0.99,
        "recommendation_hit_rate": 0.99,
        "guardrail_breaches": 0,
    }
    upstream_result = evaluate_promotion(
        current_level=3, action_class="draft", metrics=metrics, rules=RULES
    )
    downstream_result = evaluate_promotion(
        current_level=3, action_class="publish", metrics=metrics, rules=RULES
    )
    assert upstream_result.eligible is True
    assert downstream_result.eligible is False
    assert "upstream_only" in downstream_result.unmet[0]


def test_downstream_2_to_3_needs_the_extra_publication_evidence():
    metrics = {
        "window_days": 60,
        "run_count": 80,
        "gate_pass_rate": 0.90,
        "recommendation_hit_rate": 0.80,
        "rejection_all_rate": 0.05,
        "fabricated_proof_point_events": 0,
        "material_failures": 0,
        "published_zero_correction_count": 19,  # one short of 20
    }
    result = evaluate_promotion(
        current_level=2, action_class="publish", metrics=metrics, rules=RULES
    )
    assert result.eligible is False
    assert any("published_zero_correction_count" in r for r in result.unmet)

    metrics["published_zero_correction_count"] = 20
    result = evaluate_promotion(
        current_level=2, action_class="publish", metrics=metrics, rules=RULES
    )
    assert result.eligible is True


def test_no_promotion_rule_past_the_top_level():
    result = evaluate_promotion(current_level=4, action_class="draft", metrics={}, rules=RULES)
    assert result.eligible is False


def test_material_failure_always_fires_regardless_of_other_signals():
    fired = evaluate_demotion(signals={"material_failure": True}, rules=RULES)
    triggers = {r.trigger for r in fired}
    assert "material_failure" in triggers
    action = next(r.action for r in fired if r.trigger == "material_failure")
    assert action == "drop_one_level_and_pause_until_card"


def test_gate_pass_rate_below_floor_with_enough_runs_fires():
    fired = evaluate_demotion(
        signals={"gate_pass_rate": 0.40, "run_count": 25}, rules=RULES
    )
    assert any(r.trigger == "gate_pass_rate_lt" for r in fired)


def test_gate_pass_rate_below_floor_but_not_enough_runs_does_not_fire():
    fired = evaluate_demotion(
        signals={"gate_pass_rate": 0.40, "run_count": 5}, rules=RULES
    )
    assert not any(r.trigger == "gate_pass_rate_lt" for r in fired)


def test_healthy_signals_fire_nothing():
    fired = evaluate_demotion(
        signals={
            "material_failure": False,
            "gate_pass_rate": 0.95,
            "run_count": 100,
            "recommendation_hit_rate": 0.90,
            "decision_count": 100,
            "rejection_all_rate": 0.02,
            "fabricated_proof_point_rate": 0.0,
        },
        rules=RULES,
    )
    assert fired == []


def test_multiple_demotion_triggers_can_fire_together():
    fired = evaluate_demotion(
        signals={
            "material_failure": True,
            "gate_pass_rate": 0.10,
            "run_count": 100,
            "decision_count": 100,
        },
        rules=RULES,
    )
    triggers = {r.trigger for r in fired}
    assert "material_failure" in triggers
    assert "gate_pass_rate_lt" in triggers


def test_default_on_timeout_needs_level_track_and_two_earned_windows():
    earned = default_on_timeout_earned(
        level=2,
        action_class="draft",
        consecutive_promotion_windows_met=2,
        rules=RULES,
    )
    assert earned is True

    not_enough_windows = default_on_timeout_earned(
        level=2,
        action_class="draft",
        consecutive_promotion_windows_met=1,
        rules=RULES,
    )
    assert not_enough_windows is False

    level_too_low = default_on_timeout_earned(
        level=1,
        action_class="draft",
        consecutive_promotion_windows_met=5,
        rules=RULES,
    )
    assert level_too_low is False


def test_default_on_timeout_never_available_downstream():
    # "downstream never defaults; ever" -- the YAML's own words.
    never_earned = default_on_timeout_earned(
        level=4,
        action_class="publish",
        consecutive_promotion_windows_met=99,
        rules=RULES,
    )
    assert never_earned is False


def test_unclassified_action_class_raises():
    with pytest.raises(ValueError, match="neither upstream nor downstream"):
        action_class_track("not_a_real_action_class", RULES)
