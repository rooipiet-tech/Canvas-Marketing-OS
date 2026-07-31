"""AC-09 — mcp-buffer's create_draft remains structurally incapable of
setting a publish/schedule/send status; no new code path added this
session (Publisher's buffer_client.py) breaks that draft-only guarantee.

Static checks only (no cross-service subprocess re-run here — that would
require mcp-buffer's own dependency set installed inside Publisher's test
environment, a cross-cutting dependency this file deliberately avoids;
mcp/tests/test_buffer_surface.py's own suite is unmodified this session —
`git diff main -- mcp/tests/test_buffer_surface.py` is empty — and
continues to run in its own service's test job):
  (1) mcp-buffer/app/dispatch.py still hardcodes _CREATE_DRAFT_STATUS =
      "draft" (unchanged).
  (2) Publisher's new buffer_client.py's create_draft() signature accepts
      exactly channel_id and text -- the actual runtime argument-capture
      proof (nothing else is ever sent) lives in test_buffer_client.py's
      test_create_draft_sends_only_channel_id_and_text_never_a_status_argument.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BUFFER_CLIENT_PATH = Path(__file__).resolve().parents[1] / "app" / "buffer_client.py"
MCP_BUFFER_DISPATCH_PATH = REPO_ROOT / "mcp" / "mcp-buffer" / "app" / "dispatch.py"


def test_create_draft_status_hardcoded_never_caller_supplied() -> None:
    dispatch_source = MCP_BUFFER_DISPATCH_PATH.read_text(encoding="utf-8")
    assert "_CREATE_DRAFT_STATUS" in dispatch_source
    assert re.search(r'_CREATE_DRAFT_STATUS\s*=\s*"draft"', dispatch_source)

    buffer_client_source = BUFFER_CLIENT_PATH.read_text(encoding="utf-8")
    assert "def create_draft(self, *, channel_id: str, text: str)" in buffer_client_source
