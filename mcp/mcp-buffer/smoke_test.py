#!/usr/bin/env python3
"""mcp-buffer smoke test (AC-6).

  - BUFFER_API_KEY unset (default): exits 0, performs NO network call.
  - BUFFER_API_KEY set to a synthetic dummy value (with BUFFER_API_URL
    optionally pointed at a local mock server standing in for the real
    Buffer API): performs exactly ONE real-shaped call, always the
    read-only list_queue operation — never create_draft, and never
    anything publish-shaped.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.dispatch import list_queue  # noqa: E402
from mcp_common import is_live_mode  # noqa: E402


def main() -> int:
    if not is_live_mode("BUFFER_API_KEY", "buffer-api-key"):
        print("BUFFER_API_KEY not set - skipping live smoke test (fixture mode is default).")
        return 0

    result = list_queue({})
    print(f"live list_queue smoke call ok: source={result.get('source')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
