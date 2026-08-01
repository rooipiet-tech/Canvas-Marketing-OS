"""PV2-03 residual-risk mitigation: services/publisher/app/config.py's
AGENT_NAME_LOOP_PROOF and services/orchestrator/orchestrator/dispatch.py's
matching literal must stay byte-identical -- the two services share no
library (Publisher/orchestrator import nothing from each other), so this
same-value assertion is the only thing standing between the two literals
silently drifting apart.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
PUBLISHER_CONFIG_PATH = Path(__file__).resolve().parents[1] / "app" / "config.py"
ORCHESTRATOR_DISPATCH_PATH = (
    REPO_ROOT / "services" / "orchestrator" / "orchestrator" / "dispatch.py"
)

_ASSIGNMENT_RE = re.compile(r'AGENT_NAME_LOOP_PROOF\s*=\s*"([^"]+)"')


def _extract(path: Path) -> str:
    match = _ASSIGNMENT_RE.search(path.read_text(encoding="utf-8"))
    assert match, f"AGENT_NAME_LOOP_PROOF assignment not found in {path}"
    return match.group(1)


def test_agent_name_loop_proof_matches_orchestrator() -> None:
    publisher_value = _extract(PUBLISHER_CONFIG_PATH)
    orchestrator_value = _extract(ORCHESTRATOR_DISPATCH_PATH)
    assert publisher_value == orchestrator_value == "loop-proof-circuit"


def test_publisher_config_importable_constant_matches() -> None:
    from app.config import AGENT_NAME_LOOP_PROOF

    assert AGENT_NAME_LOOP_PROOF == "loop-proof-circuit"
