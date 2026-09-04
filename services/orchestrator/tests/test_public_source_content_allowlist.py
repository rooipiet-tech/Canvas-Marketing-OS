"""The redaction-firewall exemption may not widen without a reviewed edit.

`content_class="public_source_content"` removes the `full-name-like` pattern
from the model-gateway redaction firewall for that request — the only control
covering "a person or client organisation name in free text"
(contracts/model-gateway/redaction-rules.yaml), i.e. exactly the client names
docs/permission-register.yaml holds at default-deny.

dispatch.py:586-591 states the rule: no handler may set it "without its own
equivalent, explicit Pieter sign-off recorded in its own docstring". Until now
nothing enforced that. Audit finding 01-security-and-data #2 (issue #135)
found the exemption had grown from one call site to nine, three of them with
no record at all — a guard that is only a comment fails exactly this way.

This test does not judge whether any given site is justified. It pins the SET,
so a tenth site cannot appear without a reviewer editing the list below.
"""

from __future__ import annotations

import ast
import pathlib

DISPATCH = pathlib.Path(__file__).resolve().parents[1] / "orchestrator" / "dispatch.py"

EXEMPTION = "public_source_content"

# Every function that sets the exemption today.
#
# SIGNED OFF — named rulings recorded in services/model-gateway/redaction.py's
# INCIDENT 1-3 notes and in each function's own docstring:
SIGNED_OFF = {
    "_complete_ingest_with_redaction_fallback",
    "qa_review_handler",
    "draft_research_brief_handler",
    "_single_draft_qa_review",
    "_draft_social_post_handler",
    # Not in redaction.py's list, but carries its own reasoned justification
    # in its docstring (round 23, F-CONTENT-REPURPOSE-RACE).
    "draft_content_repurpose_handler",
    # Appendix D PR 5. Not a new ruling either -- both cite F-WEEKLY-LOOP-
    # DRAFT-PUBLIC-SOURCE directly in their own docstrings, as the
    # identical Fn 02/Fn 48 QA pair over the same brief-derived text
    # _single_draft_qa_review/_draft_social_post_handler already send
    # under that ruling.
    "compose_options_handler",
    "_run_option_qa",
}

# NOT SIGNED OFF — present in the code, no exemption rationale in the
# docstring and no ruling in redaction.py. Listed here so this test passes
# against today's tree rather than going red on main, and so that they are
# visible rather than silent. Each needs either a recorded sign-off or
# reverting; removing an entry from this set is the intended way to close it.
UNRECORDED = {
    "competitive_response_strategize_handler",
    "_regenerate_draft_content",
    "_run_single_qa_check",
}

ALLOWED = SIGNED_OFF | UNRECORDED


def _functions_setting_the_exemption() -> set[str]:
    """Every function in dispatch.py that sets the exemption, by either form:
    a `content_class=` keyword argument, or an assignment to a local
    `content_class` that is later passed on (qa_review_handler does this).
    """
    tree = ast.parse(DISPATCH.read_text(encoding="utf-8"))
    found: set[str] = set()

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for child in ast.walk(node):
            # content_class="public_source_content" as a call keyword
            if isinstance(child, ast.Call):
                for kw in child.keywords:
                    if (
                        kw.arg == "content_class"
                        and isinstance(kw.value, ast.Constant)
                        and kw.value.value == EXEMPTION
                    ):
                        found.add(node.name)
            # content_class = "public_source_content" as an assignment
            if isinstance(child, ast.Assign):
                if (
                    isinstance(child.value, ast.Constant)
                    and child.value.value == EXEMPTION
                    and any(
                        isinstance(t, ast.Name) and t.id == "content_class"
                        for t in child.targets
                    )
                ):
                    found.add(node.name)
    return found


def test_no_function_sets_the_exemption_without_being_listed():
    actual = _functions_setting_the_exemption()
    added = actual - ALLOWED
    assert not added, (
        "these functions set content_class='public_source_content' without being "
        f"listed in this test: {sorted(added)}. The exemption removes the only "
        "control covering client names in free text. Per dispatch.py:586-591 it "
        "needs explicit recorded sign-off in the function's own docstring — add "
        "that, then add the name to SIGNED_OFF here."
    )


def test_the_list_does_not_outlive_the_code():
    # A stale entry is its own failure: it means an exemption was removed and
    # the list still authorises it, so the next one slips in unnoticed.
    actual = _functions_setting_the_exemption()
    stale = ALLOWED - actual
    assert not stale, (
        f"these are listed here but no longer set the exemption: {sorted(stale)}. "
        "Remove them, so the list keeps meaning what it says."
    )


def test_the_unrecorded_sites_are_tracked_and_shrinking():
    # Not a pass/fail on the sites themselves — they exist and the tree is
    # green. This pins the count so closing one is deliberate and adding one
    # to UNRECORDED instead of getting sign-off is visible in the diff.
    assert len(UNRECORDED) == 3, (
        "the number of exemption sites without recorded sign-off changed. If you "
        "closed one, lower this number. If you added one, get the sign-off "
        "dispatch.py:586-591 requires instead."
    )
