"""AC-01 — autonomy policy loading and validation.

Pure-unit: no database, so these run even when TEST_DATABASE_URL is
unreachable.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from app.policy_loader import LEVEL_DESCRIPTIONS, PolicyError, load_policy

SHIPPED_POLICY = Path(__file__).resolve().parent.parent / "policy" / "autonomy.yaml"


def _write(tmp_path: Path, text: str) -> Path:
    target = tmp_path / "autonomy.yaml"
    target.write_text(text, encoding="utf-8")
    return target


def test_loads_valid_autonomy_yaml() -> None:
    policy = load_policy(SHIPPED_POLICY)

    assert policy.version == 1
    # Fail-closed default for anything unmapped.
    assert policy.default_level == 0
    assert len(policy.entries) >= 5

    # Every level 0..4 is a documented, distinct semantic.
    assert set(LEVEL_DESCRIPTIONS) == {0, 1, 2, 3, 4}

    # entries map (function_id, action_class) -> level 0..4
    for entry in policy.entries:
        assert 0 <= entry.level <= 4

    assert policy.level_for("publish.paid_ad", "publish") == 0
    assert policy.level_for("publish.social_post", "publish") == 1
    assert policy.level_for("publish.blog_article", "publish") == 2
    assert policy.level_for("draft.social_post", "draft") == 3
    assert policy.level_for("analyse.signal", "analyse") == 4

    # Unmapped pair falls through to the fail-closed default.
    assert policy.level_for("totally.unknown", "publish") == 0


def test_shipped_policy_has_no_standing_auto_approve_path() -> None:
    """RISK-01 regression.

    /gate-check is agent-native (AC-17) and authenticates no caller beyond
    a well-formed agent_run_id UUID, so ANY level-3/4 entry whose
    action_class is `publish` is a standing auto-approve-and-issue-a-token
    path reachable by any internal caller that knows the function_id. The
    shipped policy must contain none, and must contain no deploy-time
    smoke/test function_id at all — caj-governance-smoke instead pre-seeds
    an approved approval_inbox row against the real level-1
    publish.social_post entry.
    """
    policy = load_policy(SHIPPED_POLICY)

    for entry in policy.entries:
        assert not entry.function_id.startswith(("smoke.", "test.")), (
            f"{entry.function_id} is a smoke/test function_id and must not ship "
            "in the real autonomy policy"
        )
        if entry.action_class == "publish":
            assert entry.level <= 2, (
                f"{entry.function_id} auto-approves a publish at level "
                f"{entry.level} with no human and no caller authentication"
            )

    # The removed smoke entry now resolves to the fail-closed default.
    assert policy.level_for("smoke.governance_cycle", "publish") == 0


def test_rejects_invalid_level_value(tmp_path: Path) -> None:
    out_of_range = _write(
        tmp_path,
        "version: 1\nentries:\n  - function_id: a.b\n    action_class: publish\n    level: 7\n",
    )
    with pytest.raises(PolicyError, match="between 0 and 4"):
        load_policy(out_of_range)

    not_an_integer = _write(
        tmp_path,
        "version: 1\nentries:\n  - function_id: a.b\n    action_class: publish\n    level: high\n",
    )
    with pytest.raises(PolicyError, match="must be an integer 0-4"):
        load_policy(not_an_integer)


def test_rejects_missing_required_keys(tmp_path: Path) -> None:
    missing_level = _write(
        tmp_path,
        "version: 1\nentries:\n  - function_id: a.b\n    action_class: publish\n",
    )
    with pytest.raises(PolicyError, match="missing required key"):
        load_policy(missing_level)

    missing_function_id = _write(
        tmp_path,
        "version: 1\nentries:\n  - action_class: publish\n    level: 1\n",
    )
    with pytest.raises(PolicyError, match="missing required key"):
        load_policy(missing_function_id)

    missing_version = _write(
        tmp_path,
        "entries:\n  - function_id: a.b\n    action_class: publish\n    level: 1\n",
    )
    with pytest.raises(PolicyError, match="missing required top-level key 'version'"):
        load_policy(missing_version)
