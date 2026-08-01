"""functions/02-brand-steward-qa/permission_check.py must find
docs/permission-register.yaml when running inside the deployed
orchestrator container -- not just in a full repository checkout
(output-reviewer F2).

Same "not shipped in image" bug class test_contracts_path_resolution.py
already covers for config.contracts_dir()/config.functions_dir()
(L-0062): permission_check.py's find_repo_root() walks up from its own
source file looking for pyproject.toml, which does not exist anywhere
inside the image (the image is built from services/orchestrator alone --
see Dockerfile's multi-stage build, which never COPYs pyproject.toml into
the final stage). dispatch.py's load_permission_check() dynamically
`importlib`-loads this module at RUNTIME (L-0039: a digit-prefixed/
hyphenated directory name can't be dotted-imported) the first time
client_references is non-empty -- currently dormant only because
dispatch.py hardcodes client_references=[] (plan.md's own risk register),
but this must not silently break the moment that changes.

Docker itself cannot be exercised in this environment, so these tests
cover the half that genuinely can be: the resolution logic, run against a
directory that contains nothing but the one staged file -- exactly the
situation inside the image.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
ORCHESTRATOR_DIR = REPO_ROOT / "services" / "orchestrator"
DOCKERFILE_PATH = ORCHESTRATOR_DIR / "Dockerfile"
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "orchestrator-image.yml"

PERMISSION_CHECK_SRC = REPO_ROOT / "functions" / "02-brand-steward-qa" / "permission_check.py"
REGISTER_SRC = REPO_ROOT / "docs" / "permission-register.yaml"

IMAGE_PERMISSION_REGISTER_PATH = "/app/docs/permission-register.yaml"


def _load_permission_check_module(name: str):
    """Fresh, independently-cached import of permission_check.py under a
    unique module name -- mirrors dispatch.py's load_permission_check(),
    but each test gets its own module object (and therefore its own
    _load_register() lru_cache) instead of sharing dispatch.py's single
    sys.modules entry, so tests in this file never leak register state
    into one another."""
    spec = importlib.util.spec_from_file_location(name, PERMISSION_CHECK_SRC)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_env_override_resolves_to_a_directory_holding_only_the_staged_file(
    tmp_path, monkeypatch
):
    """Nothing else from the repository needs to be reachable -- which is
    the whole point, since inside the image nothing else IS."""
    staged = tmp_path / "docs" / "permission-register.yaml"
    staged.parent.mkdir(parents=True)
    staged.write_text(REGISTER_SRC.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("PERMISSION_REGISTER_PATH", str(staged))

    module = _load_permission_check_module("test_permission_check_staged")

    assert module.register_path() == staged
    assert sorted(p.name for p in staged.parent.iterdir()) == ["permission-register.yaml"]
    assert not (tmp_path.parent / "pyproject.toml").exists()

    # Real content, real default-deny outcome -- not a stub.
    clearance = module.check_clearance("Totally Fabricated Client Co")
    assert clearance.allowed is False
    assert clearance.status == module.ABSENT


def test_simulated_image_layout_would_fail_without_the_env_var():
    """Show the defect this fixes, in the layout that produces it.

    Inside the image permission_check.py sits at
    /app/functions/02-brand-steward-qa/permission_check.py, so
    find_repo_root()'s walk lands somewhere with no pyproject.toml at all
    -- confirmed by inspecting the Dockerfile's final stage, which never
    COPYs pyproject.toml into it (only the builder stage does, and that
    stage is discarded).
    """
    fake_module_path = Path("/app/functions/02-brand-steward-qa/permission_check.py")
    for candidate in (fake_module_path.parent, *fake_module_path.parents):
        assert not (candidate / "pyproject.toml").is_file()


def test_default_resolution_still_needs_no_configuration(monkeypatch):
    """Local development, pytest, and the eval harness keep working with
    the env var unset -- the checkout-relative fallback is unchanged."""
    monkeypatch.delenv("PERMISSION_REGISTER_PATH", raising=False)

    module = _load_permission_check_module("test_permission_check_default")

    assert module.register_path() == REPO_ROOT / "docs" / "permission-register.yaml"
    assert module.register_path().is_file()
    clearance = module.check_clearance("Imperial")
    assert clearance.allowed is False
    assert clearance.status == module.UNCLEARED


def test_an_empty_env_var_falls_back_rather_than_resolving_to_cwd(monkeypatch):
    """An unset-looking PERMISSION_REGISTER_PATH ("" or whitespace) must
    not become Path("") -- which silently resolves to the current working
    directory."""
    monkeypatch.setenv("PERMISSION_REGISTER_PATH", "   ")

    module = _load_permission_check_module("test_permission_check_blank_env")

    assert module.register_path() == REPO_ROOT / "docs" / "permission-register.yaml"


def test_the_image_path_chain_is_internally_consistent():
    """A staging step that writes somewhere the Dockerfile does not COPY
    from, or a COPY that lands somewhere PERMISSION_REGISTER_PATH does not
    point at, produces an image that builds fine and only fails the first
    time a dispatch handler actually supplies a non-empty
    client_references list. That failure mode is what this check is here
    to catch, mirroring test_contracts_path_resolution.py's identical
    check for CONTRACTS_DIR/FUNCTIONS_DIR."""
    dockerfile = DOCKERFILE_PATH.read_text(encoding="utf-8")
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "docs/permission-register.yaml" in workflow
    assert "services/orchestrator/docs" in workflow
    assert "docs/permission-register.yaml" in dockerfile
    assert f"ENV PERMISSION_REGISTER_PATH={IMAGE_PERMISSION_REGISTER_PATH}" in dockerfile


@pytest.fixture(autouse=True)
def _cleanup_dynamically_loaded_modules():
    """Each test above loads permission_check.py under its own unique
    sys.modules name (never dispatch.py's shared
    'cmos_orchestrator_permission_check' name) -- drop them afterwards so
    they don't accumulate across the test session."""
    yield
    for name in list(sys.modules):
        if name.startswith("test_permission_check_"):
            del sys.modules[name]
