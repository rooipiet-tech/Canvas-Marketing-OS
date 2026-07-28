#!/usr/bin/env python3
"""mcp-web smoke test (AC-6).

mcp-web has no vendor credential (orchestrator-approved waiver, see
app/tools.py's docstring), so its AC-6 "live secret env var" is the
non-secret MCP_WEB_LIVE_MODE flag:

  - MCP_WEB_LIVE_MODE unset (default): exits 0, performs NO network call
    at all (fetch_url short-circuits to its fixture in this mode).
  - MCP_WEB_LIVE_MODE set (truthy) and MCP_WEB_SMOKE_URL set to an
    allow-listed URL (typically a local mock server standing in for a
    real target): performs exactly one real GET via fetch_url and exits 0
    on success. Never a write/publish-shaped call — fetch_url is
    inherently read-only.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.tools import fetch_url  # noqa: E402
from mcp_common import flag_enabled  # noqa: E402


def main() -> int:
    if not flag_enabled("MCP_WEB_LIVE_MODE"):
        print("MCP_WEB_LIVE_MODE not set - skipping live smoke test (fixture mode is default).")
        return 0

    url = os.environ.get("MCP_WEB_SMOKE_URL")
    if not url:
        print("MCP_WEB_LIVE_MODE is set but MCP_WEB_SMOKE_URL is not - nothing to smoke-test.")
        return 0

    result = fetch_url({"url": url})
    print(f"live fetch_url smoke call ok: status_code={result.get('status_code')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
