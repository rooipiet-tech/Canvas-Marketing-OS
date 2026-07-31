"""AC-16: mcp-canva's manifest exposes template-locked asset creation (no
tool permits blank/free-form design creation without a template/brand
-template id), a bulk-create-from-CSV tool, and an export tool.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.mcp_canva_surface

MCP_ROOT = Path(__file__).resolve().parent.parent
TOOLS_YAML = MCP_ROOT / "mcp-canva" / "tools.yaml"


def _manifest() -> dict:
    return yaml.safe_load(TOOLS_YAML.read_text(encoding="utf-8"))


def test_every_design_creation_tool_requires_a_template_id(server_app):
    # inputSchema is authoritative only in app.main's TOOLS list (AC-14's
    # canonical tools.schema.json — session/s6-registry's generic
    # registry contract — forbids extra properties on a tools.yaml tool
    # entry, so inputSchema is never duplicated there; see tools.yaml's
    # header). The manifest (_manifest()) is still checked separately for
    # tool-name presence below.
    module = server_app("mcp-canva")
    creation_tools = [t for t in module.mcp_server.tools if "create" in t.name]
    assert creation_tools, "expected at least one design-creation tool"
    for tool in creation_tools:
        required = tool.inputSchema.get("required", [])
        assert any(
            "template" in r for r in required
        ), f"{tool.name} does not require a template/brand-template id"


def test_bulk_create_from_csv_tool_exists(server_app):
    module = server_app("mcp-canva")
    bulk_tools = [
        t
        for t in module.mcp_server.tools
        if "bulk" in t.name.lower() and "csv" in t.name.lower()
    ]
    assert bulk_tools, "expected a bulk-create-from-CSV tool"
    properties = bulk_tools[0].inputSchema.get("properties", {})
    assert "rows" in properties


def test_export_tool_exists():
    manifest = _manifest()
    export_tools = [t for t in manifest["tools"] if "export" in t["name"].lower()]
    assert export_tools, "expected an export tool"
