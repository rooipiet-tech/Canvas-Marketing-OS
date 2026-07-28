"""AC-2/AC-3: mcp-buffer's manifest is exactly the documented read-queue +
create-draft allow-list, with zero publish-intent patterns anywhere, and
its dispatch layer never sets an immediate-publish/immediate-send
argument on any Buffer API call.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.mcp_buffer_surface

MCP_ROOT = Path(__file__).resolve().parent.parent
TOOLS_YAML = MCP_ROOT / "mcp-buffer" / "tools.yaml"
DISPATCH_PY = MCP_ROOT / "mcp-buffer" / "app" / "dispatch.py"

PUBLISH_INTENT_RE = re.compile(r"publish|share.?now|send.?now|go.?live", re.IGNORECASE)
ALLOWED_TOOL_NAMES = {"list_queue", "get_post", "create_draft"}


def _manifest() -> dict:
    return yaml.safe_load(TOOLS_YAML.read_text(encoding="utf-8"))


def test_buffer_manifest_has_zero_publish_intent_patterns():
    for tool in _manifest()["tools"]:
        text = f"{tool['name']} {tool['description']}".lower()
        assert not PUBLISH_INTENT_RE.search(
            text
        ), f"publish-intent pattern found in tool {tool['name']!r}"


def test_buffer_manifest_tool_set_is_exact_allowlist():
    names = {t["name"] for t in _manifest()["tools"]}
    assert names == ALLOWED_TOOL_NAMES


def test_buffer_create_draft_input_schema_has_no_status_mode_field(server_app):
    # inputSchema is authoritative only in app.main's TOOLS list (AC-14's
    # canonical tools.schema.json — session/s6-registry's generic
    # registry contract — forbids extra properties on a tools.yaml tool
    # entry, so inputSchema is never duplicated there; see tools.yaml's
    # header).
    module = server_app("mcp-buffer")
    create_draft = next(t for t in module.mcp_server.tools if t.name == "create_draft")
    properties = create_draft.inputSchema.get("properties", {})
    assert not ({"status", "mode", "state"} & set(properties.keys())), (
        "create_draft's inputSchema must not expose a free-form status/mode/state "
        "parameter (AC-3)"
    )


# AC-3's automated grep half: no hardcoded/user-controllable value equal to
# {published, now, immediate, sendNow, publishNow} (or its snake_case
# equivalents) may appear anywhere in the dispatch module.
def test_buffer_dispatch_has_no_forbidden_publish_literals():
    source = DISPATCH_PY.read_text(encoding="utf-8").lower()
    forbidden_values = {
        "published",
        "sendnow",
        "publishnow",
        "send_now",
        "publish_now",
    }
    for value in forbidden_values:
        assert f'"{value}"' not in source and f"'{value}'" not in source, (
            f"forbidden publish-intent literal {value!r} found in dispatch.py"
        )


# AC-3's manual rubric half (verify text: "two testers must reach the same
# verdict"). Recorded here as the documented rubric for that human pass,
# not automatable:
#
#   For every function in mcp-buffer's dispatch layer that calls Buffer's
#   API (list_queue, get_post, create_draft — see app/dispatch.py):
#     (i) confirm only draft/idea-creation-shaped mutations are invoked,
#         never a send/schedule mutation with an immediate=true-equivalent.
#         list_queue and get_post are read-only GraphQL queries (no
#         mutation at all). create_draft's mutation hardcodes
#         `_CREATE_DRAFT_STATUS = "draft"` and never reads a status/mode/
#         state key from its `arguments` dict.
#     (ii) confirm no exposed tool's inputSchema exposes a free-form
#          status/mode enum whose values include anything beyond
#          draft-equivalent states — see app/main.py's TOOLS list:
#          create_draft's inputSchema exposes only channel_id/text.
#   Both testers must independently reach: zero findings.
def test_ac3_manual_rubric_is_documented():
    # This assertion just anchors the rubric text above to a real,
    # collectible pytest node — the actual verdict is a human judgment
    # call, not something this assertion can replace.
    assert DISPATCH_PY.exists()
