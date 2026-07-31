"""AC-14: each server's tools.yaml validates against the canonical
contracts/function-definition/tools.schema.json.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

pytestmark = pytest.mark.mcp_manifest_schema

MCP_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = MCP_ROOT.parent
SCHEMA_PATH = REPO_ROOT / "contracts" / "function-definition" / "tools.schema.json"

SERVERS = ["mcp-web", "mcp-buffer", "mcp-canva"]


@pytest.fixture(scope="module")
def schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


@pytest.mark.parametrize("server_name", SERVERS)
def test_tools_yaml_validates_against_schema(server_name, schema):
    tools_yaml_path = MCP_ROOT / server_name / "tools.yaml"
    manifest = yaml.safe_load(tools_yaml_path.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    errors = list(validator.iter_errors(manifest))
    assert errors == [], f"{server_name}/tools.yaml failed schema validation: {errors}"
