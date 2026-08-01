"""AC-24 — function 09's signal-gathering for ingest-signals is proven
end-to-end using only mcp-web's fetch_url tool; the task still reaches a
successful COMPLETED state using fetch_url alone.

mcp-web structurally implements ONLY fetch_url (web_search/
vault_signal_lookup are named-but-unimplemented, AC-24's own scope note)
— TOOLS is a closed list, so it is impossible for mcp-web's own dispatch
to route to any other tool; this is asserted directly, statically,
alongside the live proof that ingest-signals still reaches completed.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
MCP_WEB_ROOT = REPO_ROOT / "mcp" / "mcp-web"


def test_mcp_web_exposes_only_fetch_url() -> None:
    if str(MCP_WEB_ROOT) not in sys.path:
        sys.path.insert(0, str(MCP_WEB_ROOT))
    # Fresh import (mirrors mcp/conftest.py's own _purge_app_modules
    # convention) so this doesn't collide with any other test importing
    # a same-named `app` package from a different mcp-* server.
    for mod_name in list(sys.modules):
        if mod_name == "app" or mod_name.startswith("app."):
            del sys.modules[mod_name]
    import app.main as mcp_web_main

    tool_names = {t.name for t in mcp_web_main.TOOLS}
    assert tool_names == {"fetch_url"}, f"mcp-web must expose ONLY fetch_url, found {tool_names}"


def test_ingest_signals_reaches_completed_using_fetch_url_alone(
    live_daily_loop_run: dict[str, Any],
) -> None:
    final_state = live_daily_loop_run["final_state"]
    ingest_stage = next(
        (s for s in final_state.get("stages", []) if s["task_type"] == "ingest-signals"), None
    )
    assert ingest_stage is not None, "no ingest-signals stage found in this run"
    assert ingest_stage["state"] == "completed", (
        f"ingest-signals did not reach completed: {ingest_stage['state']!r}"
    )
    result_ref = ingest_stage.get("result_ref") or {}
    assert result_ref.get("vault_signal_id"), "ingest-signals produced no vault_signal_id"
