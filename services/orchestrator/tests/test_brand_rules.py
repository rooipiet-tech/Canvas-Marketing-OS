"""Tests for orchestrator.brand_rules -- the deterministic backstop for
sa-english-spelling and unsupported-claim, added after the 2026-08-10
production run false-positive-blocked all 6 drafts on these two codes
(F-QA-DETERMINISTIC-BACKSTOP)."""

from __future__ import annotations

from orchestrator import brand_rules


class TestHasUsSpelling:
    def test_clean_sa_english_returns_empty(self):
        text = (
            "Our team modernised the reporting stack and organised the "
            "rollout across three entities."
        )
        assert brand_rules.has_us_spelling(text) == []

    def test_detects_us_spelling_variant(self):
        text = "We help clients optimize their month-end close."
        assert "optimize" in brand_rules.has_us_spelling(text)

    def test_detects_multiple_variants(self):
        text = "We analyze behavior patterns to optimize the center."
        found = brand_rules.has_us_spelling(text)
        assert "analyze" in found
        assert "behavior" in found
        assert "optimize" in found
        assert "center" in found

    def test_is_case_insensitive(self):
        text = "Optimize your reporting."
        assert "optimize" in brand_rules.has_us_spelling(text)

    def test_does_not_false_positive_on_analysis(self):
        # "analysis" (the noun) is correct SA/UK English -- only the verb
        # "analyse"/"analyze" differs. This was a bug caught during
        # authoring of this module; regression-guard it explicitly.
        text = "This analysis covers three business units."
        assert brand_rules.has_us_spelling(text) == []

    def test_does_not_false_positive_on_unrelated_words(self):
        text = "The centre of the organisation runs on Microsoft Fabric."
        assert brand_rules.has_us_spelling(text) == []


class TestHasUnsupportedClaim:
    def test_bare_superlative_is_flagged(self):
        text = "We are the leading provider of embedded analytics teams."
        assert "leading" in brand_rules.has_unsupported_claim(text)

    def test_superlative_anchored_by_number_is_not_flagged(self):
        text = "We are the leading provider for 40+ business units across 14 ERP systems."
        assert brand_rules.has_unsupported_claim(text) == []

    def test_superlative_anchored_by_artefact_is_not_flagged(self):
        text = "See the leading case study on our Fabric migration."
        assert brand_rules.has_unsupported_claim(text) == []

    def test_clean_text_returns_empty(self):
        text = "Month-end closed 2 days faster across 8 entities in 3 countries."
        assert brand_rules.has_unsupported_claim(text) == []

    def test_anchor_in_other_sentence_does_not_count(self):
        # The number needs to be in the SAME sentence as the superlative.
        text = "We serve 8 entities in 3 countries. We are the leading provider in the region."
        assert "leading" in brand_rules.has_unsupported_claim(text)


class TestReconcileViolations:
    def test_drops_sa_spelling_when_text_is_clean(self):
        violations = ["sa-english-spelling"]
        text = "We modernised the reporting stack for a beverage group."
        reconciled, dropped = brand_rules.reconcile_violations(violations, text)
        assert reconciled == []
        assert dropped == ["sa-english-spelling"]

    def test_keeps_sa_spelling_when_text_has_us_variant(self):
        violations = ["sa-english-spelling"]
        text = "We help you optimize your reporting stack."
        reconciled, dropped = brand_rules.reconcile_violations(violations, text)
        assert reconciled == ["sa-english-spelling"]
        assert dropped == []

    def test_drops_unsupported_claim_when_anchored(self):
        violations = ["unsupported-claim"]
        text = "We are the leading provider for 40+ business units."
        reconciled, dropped = brand_rules.reconcile_violations(violations, text)
        assert reconciled == []
        assert dropped == ["unsupported-claim"]

    def test_keeps_unsupported_claim_when_bare(self):
        violations = ["unsupported-claim"]
        text = "We are the leading provider of embedded analytics teams."
        reconciled, dropped = brand_rules.reconcile_violations(violations, text)
        assert reconciled == ["unsupported-claim"]
        assert dropped == []

    def test_never_adds_a_violation(self):
        # This module only removes -- it must never introduce a code the
        # model didn't already assert, even if the text looks bad.
        violations = []
        text = "We optimize behavior at the center, the leading approach."
        reconciled, dropped = brand_rules.reconcile_violations(violations, text)
        assert reconciled == []
        assert dropped == []

    def test_leaves_other_violation_codes_untouched(self):
        violations = ["missing-cta", "sa-english-spelling", "url-utm"]
        text = "Clean SA English copy with no US spelling variants at all."
        reconciled, dropped = brand_rules.reconcile_violations(violations, text)
        assert reconciled == ["missing-cta", "url-utm"]
        assert dropped == ["sa-english-spelling"]

    def test_reconciles_both_codes_independently(self):
        violations = ["sa-english-spelling", "unsupported-claim"]
        text = "Clean SA English copy grounded in 8 entities across 3 countries."
        reconciled, dropped = brand_rules.reconcile_violations(violations, text)
        assert reconciled == []
        assert sorted(dropped) == ["sa-english-spelling", "unsupported-claim"]
