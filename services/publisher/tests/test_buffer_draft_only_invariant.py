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
  (2) Publisher's buffer_client.py's create_draft() exposes no
      status/mode/state parameter -- the actual runtime argument-capture
      proof (nothing else is ever sent) lives in test_buffer_client.py's
      test_create_draft_sends_only_channel_id_and_text_never_a_status_argument.

CHECK (2) WAS REWRITTEN FOR A1 (2 Sep 2026), and the rewrite makes it
stricter rather than looser. It used to be a substring match on the
literal text `def create_draft(self, *, channel_id: str, text: str)`,
which pinned the argument COUNT. The count was never the safety
property: AC-09 is that nothing in this call can transition a post's
state. A1 adds two opaque attribution labels (utm_campaign,
post_archetype) -- neither is a status, and the old assertion would have
failed on them while still passing if someone had renamed `text` to
`mode`.

It now inspects the real signature and asserts the invariant directly:
no parameter whose name contains status/mode/state, channel_id and text
still required, and everything else optional. That is a property the old
string match could not express.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

from app.buffer_client import BufferClient

REPO_ROOT = Path(__file__).resolve().parents[3]
BUFFER_CLIENT_PATH = Path(__file__).resolve().parents[1] / "app" / "buffer_client.py"
MCP_BUFFER_DISPATCH_PATH = REPO_ROOT / "mcp" / "mcp-buffer" / "app" / "dispatch.py"

FORBIDDEN_PARAM_SUBSTRINGS = ("status", "mode", "state")


def test_create_draft_status_hardcoded_never_caller_supplied() -> None:
    dispatch_source = MCP_BUFFER_DISPATCH_PATH.read_text(encoding="utf-8")
    assert "_CREATE_DRAFT_STATUS" in dispatch_source
    assert re.search(r'_CREATE_DRAFT_STATUS\s*=\s*"draft"', dispatch_source)


def test_create_draft_exposes_no_status_mode_or_state_parameter() -> None:
    parameters = inspect.signature(BufferClient.create_draft).parameters

    offenders = [
        name
        for name in parameters
        if any(forbidden in name.lower() for forbidden in FORBIDDEN_PARAM_SUBSTRINGS)
    ]
    assert not offenders, (
        f"create_draft exposes {offenders} -- AC-09 requires that no caller-supplied "
        "argument can transition a post's state"
    )


def test_create_draft_requires_only_channel_id_and_text() -> None:
    """Everything beyond the two required arguments must be optional.

    This is what stops the attribution labels becoming a precondition for
    publishing: an asset with no archetype still publishes, and a
    reporting label can never refuse a publish.
    """
    parameters = inspect.signature(BufferClient.create_draft).parameters

    required = {
        name
        for name, p in parameters.items()
        if name != "self" and p.default is inspect.Parameter.empty
    }
    assert required == {"channel_id", "text"}
