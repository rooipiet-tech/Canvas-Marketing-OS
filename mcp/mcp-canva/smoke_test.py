#!/usr/bin/env python3
"""mcp-canva smoke test (AC-6).

  - CANVA_CLIENT_ID / CANVA_CLIENT_SECRET unset (default): exits 0,
    performs NO network call.
  - Both set to synthetic dummy values (with CANVA_API_URL optionally
    pointed at a local mock server standing in for the real Canva API):
    performs exactly ONE real-shaped call, always the read-only
    smoke_read_check helper (GET-shaped "list designs") — never
    create_design_from_template, bulk_create_from_csv, or export_design,
    since AC-6 requires a read-only call and none of those three exposed
    tools is read-only-shaped.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.dispatch import _live_mode, smoke_read_check  # noqa: E402


def main() -> int:
    if not _live_mode():
        print(
            "CANVA_CLIENT_ID/CANVA_CLIENT_SECRET not set - skipping live smoke test "
            "(fixture mode is default)."
        )
        return 0

    result = smoke_read_check()
    print(f"live smoke_read_check ok: source={result.get('source')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
