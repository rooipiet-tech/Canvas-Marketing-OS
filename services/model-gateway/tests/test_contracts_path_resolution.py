"""N1 — the gateway must find its frozen contract files inside the container.

The image is built from ``services/model-gateway`` alone, so there is no
repository checkout wrapped around the code and the default "walk two
directories up, look for contracts/" resolution lands nowhere. The image
therefore stages the two files the gateway reads at runtime and points
CONTRACTS_DIR at them (Dockerfile + deploy-gateway.yml's staging step).

Docker itself cannot be exercised in this environment, so these tests cover
the half that genuinely can be: the resolution logic both loaders share, run
against a directory that contains nothing but the two staged files — exactly
the situation inside the image. The remaining half (that `docker build`
succeeds and `docker run` starts) still wants a real build at deploy time.
"""

from __future__ import annotations

import completion
import config
import pytest
import redaction
import yaml
from conftest import OPENAPI_PATH, REDACTION_RULES_PATH, SERVICE_ROOT, completion_payload

# The in-image location. Kept in one place so a Dockerfile change that moves
# it makes the mismatch visible here rather than in production.
IMAGE_CONTRACTS_DIR = "/app/contracts"

STAGED_MARKER = "staged-marker-xyz"


@pytest.fixture(autouse=True)
def _reset_contract_caches():
    """Both loaders memoise; drop the caches around every test in this file."""
    completion.reset_validator()
    redaction.reset_rules()
    yield
    completion.reset_validator()
    redaction.reset_rules()


def _stage(contracts_dir):
    """Write the two files exactly where the Dockerfile's COPY puts them.

    Each staged copy is deliberately *altered*: if a loader were still
    reading the repository checkout instead of this directory, the assertions
    below would silently pass against the unmodified original.
    """
    staged = contracts_dir / "model-gateway"
    staged.mkdir(parents=True)

    spec = yaml.safe_load(OPENAPI_PATH.read_text(encoding="utf-8"))
    spec["components"]["schemas"]["CompletionRequest"]["required"].append("staged_marker")
    (staged / "openapi.yaml").write_text(yaml.safe_dump(spec), encoding="utf-8")

    rules = yaml.safe_load(REDACTION_RULES_PATH.read_text(encoding="utf-8"))
    rules["fixtures"]["staged_markers"] = [STAGED_MARKER]
    (staged / "redaction-rules.yaml").write_text(yaml.safe_dump(rules), encoding="utf-8")

    return staged


def test_both_loaders_read_the_contracts_dir_env_var(tmp_path, monkeypatch):
    contracts_dir = tmp_path / "contracts"
    staged = _stage(contracts_dir)
    monkeypatch.setenv("CONTRACTS_DIR", str(contracts_dir))

    # Resolution points at the staged files...
    assert completion.openapi_path() == staged / "openapi.yaml"
    assert redaction.redaction_rules_path() == staged / "redaction-rules.yaml"

    # ...and both loaders actually parse *those* files, not the repo's.
    violation = completion.validate_request(completion_payload())
    assert violation is not None, "the staged openapi.yaml was not the one loaded"
    assert "required" in violation

    scan = redaction.scan_request(
        {"messages": [{"role": "user", "content": f"please check {STAGED_MARKER}"}]}
    )
    assert scan.blocked is True, "the staged redaction-rules.yaml was not the one loaded"
    assert scan.matched_pattern_id == f"fixture:{STAGED_MARKER}"


def test_a_directory_holding_only_the_two_staged_files_is_enough(tmp_path, monkeypatch):
    """Nothing else from the repository needs to be reachable — which is the
    whole point, since inside the image nothing else IS."""
    contracts_dir = tmp_path / "contracts"
    staged = _stage(contracts_dir)
    monkeypatch.setenv("CONTRACTS_DIR", str(contracts_dir))

    assert sorted(p.name for p in staged.iterdir()) == [
        "openapi.yaml",
        "redaction-rules.yaml",
    ]
    assert not (tmp_path.parent / "contracts").exists()

    # Neither call raises FileNotFoundError.
    completion.validate_request({})
    redaction.load_rules()


def test_simulated_image_layout_would_fail_without_the_env_var(tmp_path, monkeypatch):
    """Show the defect this fixes, in the layout that produces it.

    Inside the image the source sits at /app/<module>.py, so the fallback's
    two-levels-up walk resolves to the filesystem root's parent-ish region —
    somewhere with no contracts/ directory at all. Every real request would
    have raised FileNotFoundError and 500'd.
    """
    app_dir = tmp_path / "app"
    (app_dir / "contracts" / "model-gateway").mkdir(parents=True)
    (app_dir / "completion.py").write_text("# stand-in for the copied source\n", encoding="utf-8")

    fallback_root = (app_dir / "completion.py").resolve().parents[2]
    assert not (fallback_root / "contracts").exists()

    monkeypatch.setenv("CONTRACTS_DIR", str(app_dir / "contracts"))
    assert config.contracts_dir() == app_dir / "contracts"


def test_default_resolution_still_needs_no_configuration(monkeypatch):
    """Local development and pytest keep working with the env var unset."""
    monkeypatch.delenv("CONTRACTS_DIR", raising=False)

    assert completion.openapi_path() == OPENAPI_PATH
    assert redaction.redaction_rules_path() == REDACTION_RULES_PATH
    assert completion.openapi_path().exists()
    assert redaction.redaction_rules_path().exists()
    assert completion.validate_request(completion_payload()) is None


def test_an_empty_env_var_falls_back_rather_than_resolving_to_cwd(monkeypatch):
    """An unset-looking CONTRACTS_DIR ("" or whitespace) must not become
    Path("") — which silently resolves to the current working directory."""
    monkeypatch.setenv("CONTRACTS_DIR", "   ")
    assert completion.openapi_path() == OPENAPI_PATH


def test_the_image_path_chain_is_internally_consistent():
    """Docker cannot be run here, so assert the three files agree on paths.

    A staging step that writes somewhere the Dockerfile does not COPY from,
    or a COPY that lands somewhere CONTRACTS_DIR does not point at, produces
    an image that builds fine and fails on its first request. That failure
    mode is what this check is here to catch.
    """
    dockerfile = (SERVICE_ROOT / "Dockerfile").read_text(encoding="utf-8")
    workflow = (
        SERVICE_ROOT.parents[1] / ".github" / "workflows" / "deploy-gateway.yml"
    ).read_text(encoding="utf-8")

    # The build context the workflow passes to `docker build`.
    assert "services/model-gateway\n" in workflow or "services/model-gateway " in workflow
    # The staging step writes into that context, at the path the Dockerfile
    # COPYs from (context-relative `contracts/model-gateway/`).
    assert "services/model-gateway/contracts/model-gateway" in workflow
    for name in ("openapi.yaml", "redaction-rules.yaml"):
        assert f"contracts/model-gateway/{name}" in workflow
        assert f"contracts/model-gateway/{name}" in dockerfile

    # The Dockerfile lands them under WORKDIR /app and points the app there.
    assert "WORKDIR /app" in dockerfile
    assert "./contracts/model-gateway/" in dockerfile
    assert f"ENV CONTRACTS_DIR={IMAGE_CONTRACTS_DIR}" in dockerfile
