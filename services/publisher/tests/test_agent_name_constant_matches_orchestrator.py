"""PV2-03 residual-risk mitigation, updated for TD-08: services/publisher/
app/config.py and services/orchestrator/orchestrator/dispatch.py used to
each declare their own AGENT_NAME_LOOP_PROOF literal, held in sync only by
this test re-reading both files and comparing them (the two services
share no other library). Both now import the single
governance_lib.constants.AGENT_NAME_LOOP_PROOF instead, so the two can no
longer drift independently.

This test keeps two things honest going forward:
  1. the value is still what both sides expect ("loop-proof-circuit");
  2. neither file has regressed back to a hardcoded literal -- which is
     exactly the two-copies drift this test originally existed to catch,
     just one refactor upstream of where it used to look.
"""

from __future__ import annotations

import re
from pathlib import Path

from governance_lib.constants import AGENT_NAME_LOOP_PROOF

REPO_ROOT = Path(__file__).resolve().parents[3]
PUBLISHER_CONFIG_PATH = Path(__file__).resolve().parents[1] / "app" / "config.py"
ORCHESTRATOR_DISPATCH_PATH = (
    REPO_ROOT / "services" / "orchestrator" / "orchestrator" / "dispatch.py"
)

# A bare `AGENT_NAME_LOOP_PROOF = "..."` assignment would be exactly the
# regression this test exists to catch -- re-introducing an independent
# literal instead of importing the shared constant.
_HARDCODED_ASSIGNMENT_RE = re.compile(r'^\s*AGENT_NAME_LOOP_PROOF\s*=\s*"', re.MULTILINE)
_IMPORT_RE = re.compile(r"from governance_lib\.constants import[^\n]*\bAGENT_NAME_LOOP_PROOF\b")


def _assert_imports_from_governance_lib(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    assert _IMPORT_RE.search(source), (
        f"{path} does not import AGENT_NAME_LOOP_PROOF from governance_lib.constants"
    )
    assert not _HARDCODED_ASSIGNMENT_RE.search(source), (
        f"{path} re-declares AGENT_NAME_LOOP_PROOF as a literal -- this is exactly the "
        "two-copies drift TD-08 removed; import it from governance_lib.constants instead"
    )


def test_publisher_config_imports_from_governance_lib() -> None:
    _assert_imports_from_governance_lib(PUBLISHER_CONFIG_PATH)


def test_orchestrator_dispatch_imports_from_governance_lib() -> None:
    _assert_imports_from_governance_lib(ORCHESTRATOR_DISPATCH_PATH)


def test_agent_name_loop_proof_value() -> None:
    assert AGENT_NAME_LOOP_PROOF == "loop-proof-circuit"


def test_publisher_config_importable_constant_matches() -> None:
    from app.config import AGENT_NAME_LOOP_PROOF as publisher_value

    assert publisher_value == AGENT_NAME_LOOP_PROOF
