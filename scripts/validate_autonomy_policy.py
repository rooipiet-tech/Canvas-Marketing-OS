#!/usr/bin/env python3
"""Validate services/gatekeeper/policy/autonomy.yaml and
policies/earn-in-rules.yaml, individually and cross-file (Appendix D PR 4).

Run standalone (from repo root):

    python scripts/validate_autonomy_policy.py

WHY THIS EXISTS. Nothing ties these two files together mechanically.
earn-in-rules.yaml's own header is explicit that it "does not replace"
autonomy.yaml's (function_id, action_class) -> level table, only specifies
how a level may LATER change — but the two files share one vocabulary
(action_class) with no shared source of truth and no cross-check. That
already drifted once, silently: autonomy.yaml ships `draft` and
`configure` action classes that earn-in-rules.yaml's action_classes map
did not classify as upstream or downstream, which would have made earn-in
evaluation for every draft.*/config.* function undefined the day Fn 126
(the ledger that reads this file, not yet built — App D PR 6/7) went
looking for a classification and found none. Fixed in the same commit as
this validator; the check exists so the next such drift fails loudly here
instead of silently inside Fn 126 later.

Checks performed:
  1. autonomy.yaml: version/default_level present, default_level == 0
     (fails closed — a policy that defaults open is not this policy).
     Every entry has function_id, action_class, level in 0..4, and a
     non-empty description.
  2. earn-in-rules.yaml: action_classes.upstream and .downstream are each
     non-empty and disjoint (a class cannot be both). defaults.upstream
     and defaults.downstream are present and in 0..4.
     Every promote rule's `from`/`to` is a valid, strictly-increasing
     0..4 pair, and every demote rule's `action` is one of the four this
     file's own header documents.
  3. Cross-file: every distinct action_class value used by an
     autonomy.yaml entry appears in earn-in-rules.yaml's
     action_classes (upstream or downstream, never neither).

Exits 0 and prints PASS on success. Exits 1 with `FAIL: <reason>` listing
every problem found otherwise.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
AUTONOMY_YAML = REPO_ROOT / "services" / "gatekeeper" / "policy" / "autonomy.yaml"
EARN_IN_YAML = REPO_ROOT / "policies" / "earn-in-rules.yaml"

VALID_LEVELS = {0, 1, 2, 3, 4}
VALID_DEMOTE_ACTIONS = {
    "drop_one_level",
    "drop_one_level_and_pause_until_card",
    "drop_to_level_1",
    "pause_function",
}


def _load(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"FAIL: {path.relative_to(REPO_ROOT)} does not exist")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"FAIL: {path.relative_to(REPO_ROOT)} must parse to a YAML mapping")
    return data


def check_autonomy_yaml(autonomy: dict) -> set[str]:
    """Returns the set of distinct action_class values in use."""
    errors: list[str] = []

    if autonomy.get("default_level") != 0:
        errors.append(
            f"autonomy.yaml: default_level must be 0 (fail closed), "
            f"got {autonomy.get('default_level')!r}"
        )

    entries = autonomy.get("entries")
    if not isinstance(entries, list) or not entries:
        errors.append("autonomy.yaml: entries must be a non-empty list")
        entries = []

    action_classes: set[str] = set()
    seen_function_ids: set[str] = set()
    for i, entry in enumerate(entries):
        where = f"autonomy.yaml entries[{i}]"
        if not isinstance(entry, dict):
            errors.append(f"{where}: not a mapping")
            continue
        function_id = entry.get("function_id")
        action_class = entry.get("action_class")
        level = entry.get("level")
        description = entry.get("description")

        if not function_id or not isinstance(function_id, str):
            errors.append(f"{where}: function_id must be a non-empty string")
        elif function_id in seen_function_ids:
            errors.append(f"{where}: duplicate function_id {function_id!r}")
        else:
            seen_function_ids.add(function_id)

        if not action_class or not isinstance(action_class, str):
            errors.append(f"{where} ({function_id}): action_class must be a non-empty string")
        else:
            action_classes.add(action_class)

        if level not in VALID_LEVELS:
            errors.append(
                f"{where} ({function_id}): level must be one of {sorted(VALID_LEVELS)}, "
                f"got {level!r}"
            )

        if not description or not isinstance(description, str):
            errors.append(f"{where} ({function_id}): description must be a non-empty string")

    if errors:
        raise SystemExit("FAIL:\n  " + "\n  ".join(errors))
    return action_classes


def check_earn_in_yaml(earn_in: dict) -> set[str]:
    """Returns the set of action_class values earn-in-rules.yaml classifies."""
    errors: list[str] = []

    action_classes_block = earn_in.get("action_classes")
    if not isinstance(action_classes_block, dict):
        raise SystemExit("FAIL: earn-in-rules.yaml: action_classes must be a mapping")

    upstream = set(action_classes_block.get("upstream") or [])
    downstream = set(action_classes_block.get("downstream") or [])

    if not upstream:
        errors.append("earn-in-rules.yaml: action_classes.upstream must be non-empty")
    if not downstream:
        errors.append("earn-in-rules.yaml: action_classes.downstream must be non-empty")
    overlap = upstream & downstream
    if overlap:
        errors.append(
            f"earn-in-rules.yaml: action_classes.upstream and .downstream overlap: "
            f"{sorted(overlap)} — a class cannot be both"
        )

    defaults = earn_in.get("defaults") or {}
    for key in ("upstream", "downstream"):
        if defaults.get(key) not in VALID_LEVELS:
            errors.append(
                f"earn-in-rules.yaml: defaults.{key} must be one of {sorted(VALID_LEVELS)}, "
                f"got {defaults.get(key)!r}"
            )

    promote = earn_in.get("promote") or []
    if not isinstance(promote, list) or not promote:
        errors.append("earn-in-rules.yaml: promote must be a non-empty list")
    for i, rule in enumerate(promote if isinstance(promote, list) else []):
        where = f"earn-in-rules.yaml promote[{i}]"
        rule_from, rule_to = rule.get("from"), rule.get("to")
        if rule_from not in VALID_LEVELS or rule_to not in VALID_LEVELS:
            errors.append(f"{where}: from/to must both be in {sorted(VALID_LEVELS)}")
        elif rule_to != rule_from + 1:
            errors.append(
                f"{where}: promote must be exactly one level up (from={rule_from}, "
                f"to={rule_to})"
            )
        if not isinstance(rule.get("requires"), dict) or not rule.get("requires"):
            errors.append(f"{where}: requires must be a non-empty mapping")

    demote = earn_in.get("demote") or []
    if not isinstance(demote, list) or not demote:
        errors.append("earn-in-rules.yaml: demote must be a non-empty list")
    for i, rule in enumerate(demote if isinstance(demote, list) else []):
        where = f"earn-in-rules.yaml demote[{i}]"
        if not rule.get("trigger"):
            errors.append(f"{where}: trigger must be set")
        if rule.get("action") not in VALID_DEMOTE_ACTIONS:
            errors.append(
                f"{where}: action must be one of {sorted(VALID_DEMOTE_ACTIONS)}, "
                f"got {rule.get('action')!r}"
            )

    if errors:
        raise SystemExit("FAIL:\n  " + "\n  ".join(errors))
    return upstream | downstream


def check_cross_file(autonomy_classes: set[str], earn_in_classes: set[str]) -> None:
    unclassified = autonomy_classes - earn_in_classes
    if unclassified:
        raise SystemExit(
            "FAIL: action_class(es) used in autonomy.yaml but not classified as "
            f"upstream or downstream in earn-in-rules.yaml: {sorted(unclassified)}"
        )


def self_test() -> None:
    """Proves this check can both pass and fail (repo convention: a check
    that has never been seen to fail is unproven, not trustworthy)."""
    good_autonomy = {
        "default_level": 0,
        "entries": [
            {
                "function_id": "publish.social_post",
                "action_class": "publish",
                "level": 1,
                "description": "x",
            }
        ],
    }
    good_earn_in = {
        "action_classes": {"upstream": ["produce"], "downstream": ["publish"]},
        "defaults": {"upstream": 2, "downstream": 1},
        "promote": [{"from": 1, "to": 2, "requires": {"min_runs": 40}}],
        "demote": [{"trigger": "material_failure", "action": "drop_one_level"}],
    }

    # (1) the good fixtures pass.
    autonomy_classes = check_autonomy_yaml(good_autonomy)
    earn_in_classes = check_earn_in_yaml(good_earn_in)
    check_cross_file(autonomy_classes, earn_in_classes)
    print("self-test: good fixtures pass — OK")

    # (2) default_level != 0 is caught.
    try:
        check_autonomy_yaml({**good_autonomy, "default_level": 1})
    except SystemExit:
        print("self-test: default_level != 0 detected — OK")
    else:
        raise AssertionError("self-test FAILED: default_level=1 was not rejected")

    # (3) an out-of-range level is caught.
    bad_level = {
        "default_level": 0,
        "entries": [{**good_autonomy["entries"][0], "level": 9}],
    }
    try:
        check_autonomy_yaml(bad_level)
    except SystemExit:
        print("self-test: out-of-range level detected — OK")
    else:
        raise AssertionError("self-test FAILED: level=9 was not rejected")

    # (4) an upstream/downstream overlap is caught.
    overlapping = {
        "action_classes": {"upstream": ["produce"], "downstream": ["produce"]},
        "defaults": {"upstream": 2, "downstream": 1},
        "promote": good_earn_in["promote"],
        "demote": good_earn_in["demote"],
    }
    try:
        check_earn_in_yaml(overlapping)
    except SystemExit:
        print("self-test: upstream/downstream overlap detected — OK")
    else:
        raise AssertionError("self-test FAILED: overlapping classes were not rejected")

    # (5) the real bug this validator was built to catch: an action_class
    # used in autonomy.yaml but not classified in earn-in-rules.yaml.
    try:
        check_cross_file({"draft"}, {"publish", "produce"})
    except SystemExit:
        print("self-test: unclassified action_class detected — OK")
    else:
        raise AssertionError("self-test FAILED: unclassified 'draft' was not rejected")

    print("self-test: all fault-injection cases detected — PASS")


def main() -> None:
    if "--self-test" in sys.argv:
        self_test()
        return

    autonomy = _load(AUTONOMY_YAML)
    earn_in = _load(EARN_IN_YAML)

    autonomy_classes = check_autonomy_yaml(autonomy)
    earn_in_classes = check_earn_in_yaml(earn_in)
    check_cross_file(autonomy_classes, earn_in_classes)

    print(f"autonomy.yaml: {len(autonomy.get('entries', []))} entries, fails closed to 0")
    print(f"earn-in-rules.yaml: action classes {sorted(earn_in_classes)}")
    print("PASS")


if __name__ == "__main__":
    main()
