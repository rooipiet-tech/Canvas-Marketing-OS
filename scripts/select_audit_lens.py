"""Pick the review lens for this week's independent system audit.

The weekly auditor (`.github/workflows/claude-system-audit.yml`) reviews the
repository through one lens per run rather than auditing everything every time:
each run stays bounded, its cost is predictable, and each lens keeps its own
long-lived tracking issue. This script decides which lens a run gets, so the
rotation is deterministic and testable rather than buried in workflow YAML.

Usage:
    python scripts/select_audit_lens.py                # this week's lens id
    python scripts/select_audit_lens.py --week 34      # the lens week 34 gets
    python scripts/select_audit_lens.py --lens 01-security-and-data
    python scripts/select_audit_lens.py --list         # every lens id
    python scripts/select_audit_lens.py --self-test    # verify the rotation
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LENS_DIR = REPO_ROOT / ".claude/skills/system-audit/lenses"
AUDIT_WORKFLOW = REPO_ROOT / ".github/workflows/claude-system-audit.yml"


def _parse_front_matter(path: Path) -> dict[str, str]:
    """Read the `---`-delimited `key: value` header at the top of a lens file."""
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError(f"{path.name}: missing front matter opening '---'")
    fields: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return fields
        key, sep, value = line.partition(":")
        if not sep:
            raise ValueError(f"{path.name}: front-matter line is not 'key: value': {line!r}")
        fields[key.strip()] = value.strip()
    raise ValueError(f"{path.name}: missing front matter closing '---'")


def load_lenses(lens_dir: Path = LENS_DIR) -> list[dict[str, str]]:
    """Return every lens, ordered by filename — filename order is rotation order."""
    paths = sorted(lens_dir.glob("*.md"))
    if not paths:
        raise SystemExit(f"no lens files found in {lens_dir}")
    lenses = []
    for path in paths:
        fields = _parse_front_matter(path)
        for required in ("id", "title"):
            if not fields.get(required):
                raise ValueError(f"{path.name}: front matter has no '{required}'")
        if fields["id"] != path.stem:
            raise ValueError(f"{path.name}: id {fields['id']!r} does not match its filename")
        lenses.append({"id": fields["id"], "title": fields["title"], "path": str(path)})
    return lenses


def select(week: int, lenses: list[dict[str, str]]) -> dict[str, str]:
    """Map an ISO week number onto a lens. Consecutive weeks give consecutive lenses."""
    return lenses[(week - 1) % len(lenses)]


def current_week(today: dt.date | None = None) -> int:
    return (today or dt.date.today()).isocalendar()[1]


def _self_test(lenses: list[dict[str, str]]) -> None:
    ids = [lens["id"] for lens in lenses]
    assert len(set(ids)) == len(ids), f"duplicate lens ids: {ids}"

    # Over one full cycle of consecutive weeks, every lens is selected exactly once.
    cycle = [select(week, lenses)["id"] for week in range(1, len(lenses) + 1)]
    assert sorted(cycle) == sorted(ids), f"a cycle does not cover every lens: {cycle}"

    # The rotation is stable across the year boundary rather than repeating a lens.
    assert select(53, lenses)["id"] == select(53 - len(lenses), lenses)["id"]

    # Every lens brief has a body, not just front matter.
    for lens in lenses:
        body = Path(lens["path"]).read_text(encoding="utf-8").split("---", 2)[2]
        assert body.strip(), f"{lens['id']}: lens file has no body"

    # The audit workflow's workflow_dispatch choice list names each lens a
    # second time. Nothing else keeps the two in sync, which is exactly the
    # drift class this repository already guards for the scan-profile
    # allow-list -- so guard it here too.
    choices = _workflow_lens_choices()
    if choices is not None:
        assert choices == {"auto", *ids}, (
            f"{AUDIT_WORKFLOW.name}'s lens choices {sorted(choices)} do not match "
            f"the lens directory {sorted(ids)}"
        )

    print(f"self-test passed: {len(lenses)} lenses, rotation covers each exactly once per cycle")


def _workflow_lens_choices() -> set[str] | None:
    """Read the `options:` list under the audit workflow's `lens` input.

    Returns None when the workflow is absent, so the rotation can still be
    self-tested from a checkout that does not carry it.
    """
    if not AUDIT_WORKFLOW.exists():
        return None
    choices: set[str] = set()
    in_options = False
    for line in AUDIT_WORKFLOW.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped == "options:":
            in_options = True
            continue
        if in_options:
            if stripped.startswith("- "):
                choices.add(stripped[2:].strip())
            elif stripped and not stripped.startswith("#"):
                break
    return choices


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week", type=int, help="ISO week to select for (default: this week)")
    parser.add_argument("--lens", help="force a specific lens id, validated against the lens dir")
    parser.add_argument("--list", action="store_true", help="print every lens id and exit")
    parser.add_argument("--self-test", action="store_true", help="verify the rotation and exit")
    args = parser.parse_args(argv)

    lenses = load_lenses()

    if args.list:
        for lens in lenses:
            print(f"{lens['id']}\t{lens['title']}")
        return 0

    if args.self_test:
        _self_test(lenses)
        return 0

    if args.lens:
        match = next((lens for lens in lenses if lens["id"] == args.lens), None)
        if match is None:
            valid = ", ".join(lens["id"] for lens in lenses)
            print(f"unknown lens {args.lens!r}; valid ids: {valid}", file=sys.stderr)
            return 2
        print(match["id"])
        return 0

    print(select(args.week if args.week is not None else current_week(), lenses)["id"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
