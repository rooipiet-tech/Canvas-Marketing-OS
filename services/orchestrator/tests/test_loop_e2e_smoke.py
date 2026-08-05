"""F-SMOKE-GREEN-REDEFINITION (2026-08-05, round 19b part 2 of Pieter's
"Both" ruling on the round 19b tension).

Context: 2026-08-05's deploy-loop-e2e-smoke run (30980067531) was the
first time the smoke test's synthetic heartbeat ever made it through the
REAL pipeline against a REAL (non-placeholder) orchestrator image with
REAL LLM-generated draft content -- and content-qa-review (function 02,
Brand Steward QA) correctly rejected an unsupported-claim superlative.
The smoke's old all-stages-must-be-"completed" bar treated that as a
failure, even though it is proof the QA gate works. Pieter's ruling was
"Both": tighten the writer prompt (PR #73, F-QA-SUPERLATIVE-GUARD) AND
redefine what "green" means here so a legitimate QA_BLOCKED verdict
counts as a pass, distinct from a genuine defect (a stuck stage, an
unrelated dead-letter, an application bug).

These tests cover `evaluate_final_state` and its two helper predicates in
orchestrator/loop_e2e_smoke.py -- the pure classification logic, kept
free of the live HTTP/Service Bus calls the rest of the module makes so
it can be exercised without a live orchestrator.
"""

from __future__ import annotations

from orchestrator.loop_e2e_smoke import (
    _is_expected_qa_block_cascade,
    _is_legitimate_qa_block,
    evaluate_final_state,
)


def _stage(task_type: str, state: str, result_ref: dict | None = None) -> dict:
    return {
        "task_id": f"{task_type}-task",
        "task_type": task_type,
        "state": state,
        "result_ref": result_ref,
    }


def _all_completed_final_state() -> dict:
    return {
        "stages": [
            _stage("ingest-signals", "completed"),
            _stage("score-signal", "completed"),
            _stage("generate-brief", "completed"),
            _stage("draft-content", "completed"),
            _stage("qa-review", "completed"),
            _stage("request-approval", "completed"),
        ]
    }


def _legitimate_qa_block_final_state() -> dict:
    return {
        "stages": [
            _stage("ingest-signals", "completed"),
            _stage("score-signal", "completed"),
            _stage("generate-brief", "completed"),
            _stage("draft-content", "completed"),
            _stage(
                "qa-review",
                "failed",
                {"pass": False, "violations": ["unsupported-claim"], "agent_run_id": "ar-1"},
            ),
            _stage("request-approval", "dead_lettered"),
        ]
    }


class TestIsLegitimateQaBlock:
    def test_true_for_qa_review_failed_with_pass_false(self):
        stage = _stage("qa-review", "failed", {"pass": False, "violations": ["unsupported-claim"]})
        assert _is_legitimate_qa_block(stage) is True

    def test_false_for_qa_review_failed_with_no_result_ref(self):
        # A stuck/mangled QA_BLOCKED write should NOT be silently accepted --
        # only a verdict shaped exactly like dispatch.py's known-good write
        # counts as "legitimate" here (F-DISPATCH-CASCADE's own caution).
        stage = _stage("qa-review", "failed", None)
        assert _is_legitimate_qa_block(stage) is False

    def test_false_for_qa_review_failed_with_pass_true(self):
        # pass=True should never coexist with state=failed in practice, but
        # the predicate must not treat it as a legitimate block regardless.
        stage = _stage("qa-review", "failed", {"pass": True})
        assert _is_legitimate_qa_block(stage) is False

    def test_false_for_qa_review_dead_lettered(self):
        # dead_lettered on qa-review itself means the retry state machine
        # gave up -- an infra/application defect, not a QA_BLOCKED verdict.
        stage = _stage("qa-review", "dead_lettered", {"pass": False})
        assert _is_legitimate_qa_block(stage) is False

    def test_false_for_non_qa_review_task_type(self):
        stage = _stage("draft-content", "failed", {"pass": False})
        assert _is_legitimate_qa_block(stage) is False

    def test_false_for_completed_qa_review(self):
        stage = _stage("qa-review", "completed", {"pass": True})
        assert _is_legitimate_qa_block(stage) is False


class TestIsExpectedQaBlockCascade:
    def test_true_for_request_approval_dead_lettered_with_legitimate_block_present(self):
        stages = _legitimate_qa_block_final_state()["stages"]
        request_approval = next(s for s in stages if s["task_type"] == "request-approval")
        assert _is_expected_qa_block_cascade(request_approval, stages) is True

    def test_false_for_request_approval_dead_lettered_with_no_legitimate_block(self):
        # dead_lettered request-approval with NO explaining QA block anywhere
        # in the lineage is an unexplained defect (e.g. a genuine infra
        # dead-letter unconnected to QA) -- must not be waved through.
        stages = [
            _stage("qa-review", "completed", {"pass": True}),
            _stage("request-approval", "dead_lettered"),
        ]
        request_approval = stages[1]
        assert _is_expected_qa_block_cascade(request_approval, stages) is False

    def test_false_for_non_request_approval_task_type(self):
        stages = _legitimate_qa_block_final_state()["stages"]
        qa_review = next(s for s in stages if s["task_type"] == "qa-review")
        assert _is_expected_qa_block_cascade(qa_review, stages) is False

    def test_false_for_request_approval_not_dead_lettered(self):
        stages = [
            _stage("qa-review", "failed", {"pass": False, "violations": ["x"]}),
            _stage("request-approval", "failed"),
        ]
        request_approval = stages[1]
        assert _is_expected_qa_block_cascade(request_approval, stages) is False


class TestEvaluateFinalState:
    def test_all_completed_passes(self):
        passed, message = evaluate_final_state(_all_completed_final_state())
        assert passed is True
        assert message.startswith("PASS:")

    def test_legitimate_qa_block_with_cascade_passes(self):
        passed, message = evaluate_final_state(_legitimate_qa_block_final_state())
        assert passed is True
        assert message.startswith("PASS (QA_BLOCKED):")
        assert "unsupported-claim" in message

    def test_legitimate_qa_block_without_downstream_stage_passes(self):
        # request-approval may not exist yet in the lineage snapshot at all
        # (e.g. the cascade hasn't landed the instant this poll fired) --
        # the qa-review block alone is still sufficient to explain things.
        final_state = {
            "stages": [
                _stage("draft-content", "completed"),
                _stage(
                    "qa-review",
                    "failed",
                    {"pass": False, "violations": ["unsupported-claim"]},
                ),
            ]
        }
        passed, message = evaluate_final_state(final_state)
        assert passed is True
        assert message.startswith("PASS (QA_BLOCKED):")

    def test_unrelated_dead_lettered_stage_fails(self):
        # A genuine infra dead-letter with NO QA block anywhere must still
        # fail the smoke -- this is exactly the "real defect" case the
        # redefinition must not paper over.
        final_state = {
            "stages": [
                _stage("ingest-signals", "dead_lettered"),
                _stage("score-signal", "dead_lettered"),
            ]
        }
        passed, message = evaluate_final_state(final_state)
        assert passed is False
        assert message.startswith("FAIL:")

    def test_qa_review_failed_without_result_ref_fails(self):
        # An ambiguous/mangled QA_BLOCKED write (missing result_ref) must
        # not be silently accepted as legitimate.
        final_state = {
            "stages": [
                _stage("draft-content", "completed"),
                _stage("qa-review", "failed", None),
            ]
        }
        passed, message = evaluate_final_state(final_state)
        assert passed is False
        assert message.startswith("FAIL:")

    def test_request_approval_dead_lettered_alone_fails(self):
        # dead_lettered request-approval with no explaining qa-review block
        # anywhere in the run must still fail -- the cascade predicate only
        # accepts it when it is actually explained.
        final_state = {
            "stages": [
                _stage("qa-review", "completed", {"pass": True}),
                _stage("request-approval", "dead_lettered"),
            ]
        }
        passed, message = evaluate_final_state(final_state)
        assert passed is False
        assert message.startswith("FAIL:")

    def test_non_qa_stage_failure_still_fails(self):
        # A genuine application bug in a non-QA stage (e.g. draft-content
        # itself erroring) must still fail the smoke -- the redefinition
        # is scoped to QA_BLOCKED only, not "any failure is fine now".
        final_state = {
            "stages": [
                _stage("draft-content", "failed"),
                _stage("qa-review", "dead_lettered"),
            ]
        }
        passed, message = evaluate_final_state(final_state)
        assert passed is False
        assert message.startswith("FAIL:")

    def test_empty_stages_passes_vacuously(self):
        # Mirrors the pre-existing behaviour: an empty stage list has no
        # non-completed stages, so it trivially passes. poll_run_state's
        # own `if stages and terminal_count == len(stages)` guard means
        # this shape should never actually reach evaluate_final_state in
        # production, but the pure function's contract stays well-defined.
        passed, message = evaluate_final_state({"stages": []})
        assert passed is True
        assert message.startswith("PASS:")
