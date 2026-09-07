"""AGENT_NAME_LOOP_PROOF — the single source of truth orchestrator/
dispatch.py and publisher/app/config.py both now import (TD-08)."""

from __future__ import annotations

from governance_lib.constants import AGENT_NAME_LOOP_PROOF


def test_agent_name_loop_proof_value() -> None:
    assert AGENT_NAME_LOOP_PROOF == "loop-proof-circuit"
