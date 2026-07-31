"""AC-06(a) — the uncleared-client-block is proven via a dedicated
invocation path, never via the live published asset (that's
test_live_content_is_client_free.py's job). Reuses function 02's OWN
permission_check.py by reference (never a duplicated/forked copy of its
logic, per AC-31) — the SAME mechanism orchestrator/dispatch.py's
qa_review_handler calls deterministically alongside the LLM-judged
checks.

No live infra needed: docs/permission-register.yaml is a local file,
read directly — this is the exact self-contained mechanism DE-4 and
AC-06 describe, independent of any deployed service.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ORCHESTRATOR_SERVICE_ROOT = REPO_ROOT / "services" / "orchestrator"
if str(ORCHESTRATOR_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR_SERVICE_ROOT))

from orchestrator import dispatch  # noqa: E402

REGISTERED_UNCLEARED_NAMES = ("Imperial", "Rotork", "Weir", "ArcelorMittal SA", "SGB Cape", "Delta")


def test_registered_uncleared_client_name_blocks() -> None:
    permission_check = dispatch.load_permission_check()
    for name in REGISTERED_UNCLEARED_NAMES:
        uncleared = permission_check.find_uncleared_references([name])
        assert uncleared, f"{name!r} is registered UNCLEARED and must block"
        assert uncleared[0].violation_code == "uncleared-client-reference"


def test_fabricated_never_registered_name_blocks_identically() -> None:
    """Absence from the register is never permission -- an unlisted name
    blocks exactly like an explicit UNCLEARED entry (DE-4's own core
    point)."""
    permission_check = dispatch.load_permission_check()
    uncleared = permission_check.find_uncleared_references(["Totally Fabricated Client Co"])
    assert uncleared
    assert uncleared[0].status == permission_check.ABSENT
    assert uncleared[0].violation_code == "uncleared-client-reference"


def test_no_client_is_cleared_today() -> None:
    """docs/permission-register.yaml has zero CLEARED entries (DE-4's
    seed-state assumption) -- if this ever flips, the live published
    content's client-free guarantee needs re-review, not a silent pass."""
    permission_check = dispatch.load_permission_check()
    for name in REGISTERED_UNCLEARED_NAMES:
        clearance = permission_check.check_clearance(name)
        assert clearance.allowed is False, f"{name} unexpectedly CLEARED"
