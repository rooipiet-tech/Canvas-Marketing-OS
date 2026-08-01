"""qa-review is invoked from TWO loop positions through ONE dispatch
handler (plan step 6/10/31): daily-signal-loop.yaml's brief-QA ('qa',
predecessor 'draft'/draft-brief) and the S8 proof circuit's content-QA
('content-qa-review', predecessor 'draft-linkedin-post'/draft-content).
Both must be independently and correctly resolved within ONE live run —
proving the depends_on-lineage-based WHAT-to-validate resolution (never
a task_id naming convention) actually works end to end, not just in the
in-memory-fake unit tests (services/orchestrator/tests/test_dispatch.py).
"""

from __future__ import annotations

from typing import Any


def test_both_qa_review_positions_resolve_and_pass_in_one_run(
    live_daily_loop_run: dict[str, Any],
) -> None:
    final_state = live_daily_loop_run["final_state"]
    qa_stages = [s for s in final_state.get("stages", []) if s["task_type"] == "qa-review"]
    assert len(qa_stages) == 2, (
        f"expected exactly 2 qa-review stages (brief-QA + proof-circuit content-QA) in one "
        f"daily-signal-loop run, found {len(qa_stages)}"
    )

    brief_qa = [s for s in qa_stages if (s.get("result_ref") or {}).get("brief_id")]
    content_qa = [s for s in qa_stages if (s.get("result_ref") or {}).get("vault_asset_id")]

    assert len(brief_qa) == 1, "expected exactly 1 brief-QA (brief_id-carrying) qa-review stage"
    assert len(content_qa) == 1, (
        "expected exactly 1 proof-circuit content-QA (vault_asset_id-carrying) qa-review stage"
    )

    for stage in qa_stages:
        assert stage["state"] == "completed", (
            f"qa-review stage {stage['task_id']} did not pass (state={stage['state']!r}) -- "
            "this run's synthetic content is deliberately clean and should never trip QA"
        )
        assert (stage.get("result_ref") or {}).get("pass") is True
