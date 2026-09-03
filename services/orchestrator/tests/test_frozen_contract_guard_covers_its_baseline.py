"""Every hash in .frozen-v1.sha256 must belong to a file the guard checks.

WHAT WENT WRONG. contracts/.frozen-v1.sha256 recorded ten paths.
scripts/validate_contracts.py's FROZEN_FILES listed seven, and `check`
iterates FROZEN_FILES -- so three contracts carried a baseline hash that
nothing ever compared against:

    orchestrator/loop-definition.schema.json
    orchestrator/heartbeat-event.schema.json
    orchestrator/dead-letter-alert.schema.json

They entered the baseline in efe75b5 (#79) and were never added to
FROZEN_FILES, so they were never guarded -- while reading, to anyone
opening the baseline file, exactly like the seven that were. A
breaking edit to any of them would have passed CI silently.

HOW IT SURFACED, which is the part worth keeping: an unrelated additive
change to vault-schema/schema.sql ran `--write-baseline`, and
write_baseline rewrites the file from FROZEN_FILES alone. The three
orphans simply vanished from the diff. Nothing announced the drift; it
showed up as an unexplained "4 deletions" in a one-line change, and only
because that looked wrong.

This is the vacuous-verification class CLAUDE.md names (L-0005, L-0046,
L-0059): a check that reports success without having checked. Pinning the
two lists against each other is what makes the baseline file mean what it
appears to mean.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "validate_contracts.py"
BASELINE = REPO_ROOT / "contracts" / ".frozen-v1.sha256"


def _frozen_files() -> list[str]:
    """FROZEN_FILES as the script really defines it.

    Imported rather than regex-scraped: a list read out of source can go
    stale against the module that actually runs (L-0080's registry
    lesson), and this guard exists precisely because two representations
    of the same set drifted apart.
    """
    spec = importlib.util.spec_from_file_location("cmos_validate_contracts", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return list(module.FROZEN_FILES)


def _baseline_paths() -> list[str]:
    paths = []
    for line in BASELINE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        _digest, _sep, rel_path = line.partition("  ")
        paths.append(rel_path.strip())
    return paths


def test_the_lists_are_not_empty():
    """Guard the guard: two empty sets compare equal and prove nothing."""
    assert len(_frozen_files()) >= 7
    assert len(_baseline_paths()) >= 7


def test_every_baseline_entry_is_actually_guarded():
    orphaned = sorted(set(_baseline_paths()) - set(_frozen_files()))
    assert not orphaned, (
        "these contract file(s) carry a baseline hash that validate_contracts.py "
        f"never compares, so a breaking edit to them passes CI silently: {orphaned}. "
        "Add them to FROZEN_FILES, or remove their baseline lines and say why they "
        "are not frozen."
    )


def test_every_guarded_file_has_a_baseline_entry():
    """The other direction: a guarded file with no hash is also unchecked.

    `check` skips a FROZEN_FILES entry that is absent from `recorded`,
    so this fails open rather than loudly -- the same shape of hole in
    the opposite direction.
    """
    unrecorded = sorted(set(_frozen_files()) - set(_baseline_paths()))
    assert not unrecorded, (
        "these file(s) are listed in FROZEN_FILES but have no recorded hash, so the "
        f"guard has nothing to compare and passes them unconditionally: {unrecorded}. "
        "Run scripts/validate_contracts.py --write-baseline."
    )
