"""Client-reference clearance check for the Case Study Writer (function 47).

Copied near-verbatim from `functions/02-brand-steward-qa/permission_check.py`
(same logic, only the docstrings' function reference updated). Reads
`docs/permission-register.yaml` and answers one question: may this client
name be used in a public case-study draft?

**Default deny.** Only an entry whose `status` is the exact string `CLEARED`
permits naming. Everything else blocks, including — and this is the whole
point — a name that does not appear in the register at all. Absence is never
read as permission.

The register path is resolved by walking up from this file to the repo root
(the directory containing `pyproject.toml`), generalising the
`Path(__file__).resolve().parent.parent` idiom used by
`scripts/validate_contracts.py` for a file nested this deep. It is never
resolved relative to the current working directory, because this module is
loaded dynamically by the eval harness from arbitrary cwds.

Run directly to execute the self-test:

    python functions/47-case-study-writer/permission_check.py
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

REPO_ROOT_MARKER = "pyproject.toml"
REGISTER_RELATIVE_PATH = "docs/permission-register.yaml"

CLEARED = "CLEARED"
UNCLEARED = "UNCLEARED"
ABSENT = "ABSENT"

VIOLATION_CODE = "uncleared-client-reference"


@dataclass(frozen=True)
class Clearance:
    """Outcome of a clearance lookup."""

    name: str
    allowed: bool
    status: str
    reason: str

    @property
    def violation_code(self) -> str | None:
        return None if self.allowed else VIOLATION_CODE


def find_repo_root(start: Path | None = None) -> Path:
    """Walk up from this file (or `start`) to the directory holding pyproject.toml."""
    current = (start or Path(__file__)).resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / REPO_ROOT_MARKER).is_file():
            return candidate
    raise RuntimeError(
        f"could not locate repo root (no {REPO_ROOT_MARKER}) walking up from {current}"
    )


def register_path() -> Path:
    """Absolute path to docs/permission-register.yaml, cwd-independent."""
    return find_repo_root() / REGISTER_RELATIVE_PATH


@lru_cache(maxsize=1)
def _load_register() -> dict[str, dict]:
    """Map every registered name and alias (lower-cased) to its entry."""
    path = register_path()
    if not path.is_file():
        # A missing register cannot mean "allow everything". Return an empty
        # index so every lookup falls through to the default-deny branch.
        return {}
    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    index: dict[str, dict] = {}
    for entry in document.get("clients") or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "")).strip()
        if not name:
            continue
        index[name.lower()] = entry
        for alias in entry.get("aliases") or []:
            alias_text = str(alias).strip()
            if alias_text:
                index.setdefault(alias_text.lower(), entry)
    return index


def check_clearance(client_name: str) -> Clearance:
    """Return the clearance decision for `client_name`. Default deny.

    A name absent from the register blocks in exactly the same way as an
    explicit UNCLEARED entry: same `allowed=False`, same violation code.
    """
    name = (client_name or "").strip()
    if not name:
        return Clearance(
            name=name,
            allowed=False,
            status=ABSENT,
            reason="empty client name — nothing to clear, so naming is blocked",
        )

    entry = _load_register().get(name.lower())
    if entry is None:
        return Clearance(
            name=name,
            allowed=False,
            status=ABSENT,
            reason=(
                f"{name!r} does not appear in {REGISTER_RELATIVE_PATH}. Absence is "
                "never permission: an unlisted name blocks identically to an "
                "explicit UNCLEARED entry."
            ),
        )

    status = str(entry.get("status", "")).strip()
    if status == CLEARED:
        return Clearance(
            name=name,
            allowed=True,
            status=CLEARED,
            reason=str(entry.get("evidence") or "CLEARED in the permission register"),
        )

    return Clearance(
        name=name,
        allowed=False,
        status=status or UNCLEARED,
        reason=(
            f"{name!r} is {status or UNCLEARED} in {REGISTER_RELATIVE_PATH}: "
            + " ".join(str(entry.get("reason") or "no written permission on file").split())
        ),
    )


def find_uncleared_references(candidate_names: list[str]) -> list[Clearance]:
    """Every supplied name that may not be used, in input order."""
    return [
        clearance
        for clearance in (check_clearance(name) for name in candidate_names)
        if not clearance.allowed
    ]


def _self_test() -> None:
    """Prove absent-from-register blocks identically to explicit UNCLEARED."""
    listed = check_clearance("Rotork")
    fabricated = check_clearance("Totally Fabricated Client Co")

    assert listed.allowed is False, "Rotork is UNCLEARED and must block"
    assert listed.status == UNCLEARED, f"expected UNCLEARED for Rotork, got {listed.status}"
    assert fabricated.allowed is False, "an unlisted name must block"
    assert fabricated.status == ABSENT, f"expected ABSENT, got {fabricated.status}"

    # The observable gating outcome must be identical for both.
    assert listed.allowed == fabricated.allowed, (
        "absent-from-register must block identically to explicit UNCLEARED"
    )
    assert listed.violation_code == fabricated.violation_code == VIOLATION_CODE, (
        "both must emit the same violation code"
    )

    # Alias resolution and case-insensitivity.
    assert check_clearance("imperial logistics").status == UNCLEARED
    assert check_clearance("ArcelorMittal").status == UNCLEARED

    # Nothing in the seeded register is CLEARED.
    for name in ("Imperial", "Rotork", "Weir", "ArcelorMittal SA", "SGB Cape", "Delta"):
        assert check_clearance(name).allowed is False, f"{name} must not be CLEARED yet"

    print(f"register: {REGISTER_RELATIVE_PATH}")
    print(f"Rotork              -> allowed={listed.allowed} status={listed.status}")
    print(f"Fabricated client   -> allowed={fabricated.allowed} status={fabricated.status}")
    print("absent-from-register blocks identically to explicit UNCLEARED")
    print("PASS")


if __name__ == "__main__":
    try:
        _self_test()
    except AssertionError as exc:
        print(f"FAIL: permission_check self-test: {exc}", file=sys.stderr)
        sys.exit(1)
