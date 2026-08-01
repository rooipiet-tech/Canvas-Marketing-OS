"""The orchestrator must find its frozen contract schemas inside the
container.

The image is built from services/orchestrator alone, so there is no
repository checkout wrapped around the code and the default "walk parent
directories, look for contracts/" resolution lands nowhere — confirmed
live, 2026-07-31: ca-orchestrator's own startup logs showed
`loop_load_failed ... No such file or directory:
'/contracts/orchestrator/loop-definition.schema.json'`, and
caj-orchestrator-smoke-test crashed with the identical FileNotFoundError
before it could publish anything. The image therefore stages the four
schema files both loaders read and points CONTRACTS_DIR at them (Dockerfile
+ orchestrator-image.yml's staging step).

Docker itself cannot be exercised in this environment, so these tests cover
the half that genuinely can be: the resolution logic both loaders share, run
against a directory that contains nothing but the staged files — exactly
the situation inside the image. The remaining half (that `docker build`
succeeds and `docker run` starts) still wants a real build at deploy time.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from orchestrator import config
from orchestrator.loop_loader import load_loop
from orchestrator.servicebus import producer

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
ORCHESTRATOR_DIR = REPO_ROOT / "services" / "orchestrator"
DOCKERFILE_PATH = ORCHESTRATOR_DIR / "Dockerfile"
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "orchestrator-image.yml"

LOOP_SCHEMA_SRC = REPO_ROOT / "contracts" / "orchestrator" / "loop-definition.schema.json"
HEARTBEAT_SCHEMA_SRC = REPO_ROOT / "contracts" / "orchestrator" / "heartbeat-event.schema.json"
DEAD_LETTER_SCHEMA_SRC = REPO_ROOT / "contracts" / "orchestrator" / "dead-letter-alert.schema.json"
TASK_ENVELOPE_SCHEMA_SRC = REPO_ROOT / "contracts" / "service-bus" / "task-envelope.schema.json"

DAILY_LOOP_PATH = ORCHESTRATOR_DIR / "loops" / "daily-signal-loop.yaml"

# The in-image location. Kept in one place so a Dockerfile change that
# moves it makes the mismatch visible here rather than in production.
IMAGE_CONTRACTS_DIR = "/app/contracts"


@pytest.fixture(autouse=True)
def _reset_producer_caches():
    """producer.py memoises its three schemas; drop the caches around every
    test in this file so staged-vs-real content is never masked."""
    producer._TASK_ENVELOPE_SCHEMA = None
    producer._HEARTBEAT_SCHEMA = None
    producer._DEAD_LETTER_SCHEMA = None
    yield
    producer._TASK_ENVELOPE_SCHEMA = None
    producer._HEARTBEAT_SCHEMA = None
    producer._DEAD_LETTER_SCHEMA = None


def _stage(contracts_dir: Path) -> None:
    """Write the four files exactly where the Dockerfile's COPY puts them."""
    (contracts_dir / "orchestrator").mkdir(parents=True)
    (contracts_dir / "service-bus").mkdir(parents=True)
    for src, dest_subdir in (
        (LOOP_SCHEMA_SRC, "orchestrator"),
        (HEARTBEAT_SCHEMA_SRC, "orchestrator"),
        (DEAD_LETTER_SCHEMA_SRC, "orchestrator"),
        (TASK_ENVELOPE_SCHEMA_SRC, "service-bus"),
    ):
        (contracts_dir / dest_subdir / src.name).write_text(
            src.read_text(encoding="utf-8"), encoding="utf-8"
        )


def test_both_loaders_read_the_contracts_dir_env_var(tmp_path, monkeypatch):
    contracts_dir = tmp_path / "contracts"
    _stage(contracts_dir)
    monkeypatch.setenv("CONTRACTS_DIR", str(contracts_dir))

    assert config.contracts_dir() == contracts_dir

    # loop_loader reads the staged schema, not the repo's.
    loop = load_loop(DAILY_LOOP_PATH)
    assert loop.loop_id == "daily-signal-loop"

    # producer's three schema getters all resolve under the staged dir.
    assert (
        producer._task_envelope_schema_path()
        == contracts_dir / "service-bus" / "task-envelope.schema.json"
    )
    assert (
        producer._heartbeat_schema_path()
        == contracts_dir / "orchestrator" / "heartbeat-event.schema.json"
    )
    assert (
        producer._dead_letter_schema_path()
        == contracts_dir / "orchestrator" / "dead-letter-alert.schema.json"
    )
    producer._task_envelope_schema()
    producer._heartbeat_schema()
    producer._dead_letter_schema()


def test_a_directory_holding_only_the_staged_files_is_enough(tmp_path, monkeypatch):
    """Nothing else from the repository needs to be reachable — which is
    the whole point, since inside the image nothing else IS."""
    contracts_dir = tmp_path / "contracts"
    _stage(contracts_dir)
    monkeypatch.setenv("CONTRACTS_DIR", str(contracts_dir))

    assert sorted(p.name for p in contracts_dir.iterdir()) == ["orchestrator", "service-bus"]
    assert not (tmp_path.parent / "contracts").exists()

    # Neither call raises FileNotFoundError.
    load_loop(DAILY_LOOP_PATH)
    producer._task_envelope_schema()


def test_simulated_image_layout_would_fail_without_the_env_var():
    """Show the defect this fixes, in the layout that produces it.

    Inside the image the package sits at /app/orchestrator/<module>.py, so
    the fallback's parents[3] walk resolves to somewhere with no
    contracts/ directory at all — this is exactly what happened live,
    2026-07-31.
    """
    fake_module = Path("/app/orchestrator/loop_loader.py")
    resolved_parents = fake_module.resolve().parents
    fallback_root = resolved_parents[3] if len(resolved_parents) > 3 else fake_module.anchor
    assert not (Path(str(fallback_root)) / "contracts" / "orchestrator").exists()


def test_default_resolution_still_needs_no_configuration(monkeypatch):
    """Local development and pytest keep working with the env var unset."""
    monkeypatch.delenv("CONTRACTS_DIR", raising=False)

    assert config.contracts_dir() == REPO_ROOT / "contracts"
    assert (config.contracts_dir() / "orchestrator" / "loop-definition.schema.json").exists()

    loop = load_loop(DAILY_LOOP_PATH)
    assert loop.loop_id == "daily-signal-loop"
    producer._task_envelope_schema()
    producer._heartbeat_schema()
    producer._dead_letter_schema()


def test_an_empty_env_var_falls_back_rather_than_resolving_to_cwd(monkeypatch):
    """An unset-looking CONTRACTS_DIR ("" or whitespace) must not become
    Path("") — which silently resolves to the current working directory."""
    monkeypatch.setenv("CONTRACTS_DIR", "   ")
    assert config.contracts_dir() == REPO_ROOT / "contracts"


def test_the_image_path_chain_is_internally_consistent():
    """A staging step that writes somewhere the Dockerfile does not COPY
    from, or a COPY that lands somewhere CONTRACTS_DIR does not point at,
    produces an image that builds fine and fails on its first loop load /
    publish. That failure mode is what this check is here to catch."""
    dockerfile = DOCKERFILE_PATH.read_text(encoding="utf-8")
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "services/orchestrator/contracts/orchestrator" in workflow
    assert "services/orchestrator/contracts/service-bus" in workflow
    for schema_path in (
        "contracts/orchestrator/loop-definition.schema.json",
        "contracts/orchestrator/heartbeat-event.schema.json",
        "contracts/orchestrator/dead-letter-alert.schema.json",
        "contracts/service-bus/task-envelope.schema.json",
    ):
        assert schema_path in workflow
        assert schema_path in dockerfile

    assert "WORKDIR /app" in dockerfile
    assert f"ENV CONTRACTS_DIR={IMAGE_CONTRACTS_DIR}" in dockerfile

    # The smoke test's golden fixture is shipped too, and it's already
    # inside the build context (services/orchestrator) — no staging step
    # needed, just a direct Dockerfile COPY.
    assert "tests/golden/daily-signal-loop.expected.json" in dockerfile


def test_no_orchestrator_module_walks_parent_directories_at_import_time():
    """Sweep for this whole bug class, not just the two fixed instances.

    A module-level `<expr>.parent...` chain or `.parents[N]` assumes the
    source tree is at a specific depth below a filesystem root — an
    assumption that holds in a repo checkout and fails inside the shallow
    image. scripts/ and tests/ are excluded: neither ships inside the
    image (scripts/ is developer/CI tooling; tests/ is explicitly excluded
    by the Dockerfile's own multi-stage build, RISK-002).
    """
    import ast

    skip_top_level = {"scripts", "tests", "orchestrator.egg-info", "build"}
    offenders: list[str] = []

    for source_file in sorted(ORCHESTRATOR_DIR.rglob("*.py")):
        rel = source_file.relative_to(ORCHESTRATOR_DIR)
        top = rel.parts[0]
        if top in skip_top_level or top.startswith(".") or "site-packages" in rel.parts:
            continue
        tree = ast.parse(source_file.read_text(encoding="utf-8"), filename=str(source_file))
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            for inner in ast.walk(node):
                if isinstance(inner, ast.Attribute) and inner.attr in ("parent", "parents"):
                    offenders.append(f"{rel.as_posix()}:{inner.lineno}")

    # Three genuinely correct module-level walks, all safe at any checkout
    # depth because they land on a path that is *always* right next to the
    # source file inside the image, not somewhere N repo-root levels away:
    #   - main.py's LOOPS_DIR = .../main.py's parent / "loops"
    #     (/app/main.py -> /app/loops)
    #   - smoke_test.py's _ROOT = .../smoke_test.py's parent.parent
    #     (/app/orchestrator/smoke_test.py -> /app, matching WORKDIR;
    #     _LOOP_PATH and _GOLDEN_PATH both resolve under it correctly
    #     because loops/ and tests/golden/ are shipped at /app/loops and
    #     /app/tests/golden respectively — see Dockerfile)
    #   - loop_e2e_smoke.py's _ROOT = .../loop_e2e_smoke.py's parent.parent
    #     (/app/orchestrator/loop_e2e_smoke.py -> /app) — the IDENTICAL
    #     shape/depth as smoke_test.py above (same directory, same
    #     WORKDIR, same shipped loops/ location), added by
    #     session/s8-first-loop's plan step 20 after this test's original
    #     two-entry allowlist was written.
    #
    # Located by name rather than a hardcoded line number (the ORIGINAL
    # form of this allowlist hardcoded "main.py:35", which broke the very
    # next time an unrelated import was added to main.py and shifted
    # LOOPS_DIR down two lines — exactly the kind of false failure this
    # sweep must never produce) — scan each known-safe file for its one
    # documented assignment target instead.
    def _line_of(path: Path, target_name: str) -> int:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == target_name
            ):
                return node.lineno
        raise AssertionError(f"{path}: no module-level `{target_name} = ...` assignment found")

    main_py_path = ORCHESTRATOR_DIR / "main.py"
    smoke_test_py_path = ORCHESTRATOR_DIR / "orchestrator" / "smoke_test.py"
    loop_e2e_smoke_py_path = ORCHESTRATOR_DIR / "orchestrator" / "loop_e2e_smoke.py"
    main_py = main_py_path.relative_to(ORCHESTRATOR_DIR).as_posix()
    smoke_test_py = smoke_test_py_path.relative_to(ORCHESTRATOR_DIR).as_posix()
    loop_e2e_smoke_py = loop_e2e_smoke_py_path.relative_to(ORCHESTRATOR_DIR).as_posix()
    permitted = {
        f"{main_py}:{_line_of(main_py_path, 'LOOPS_DIR')}",
        f"{smoke_test_py}:{_line_of(smoke_test_py_path, '_ROOT')}",
        f"{loop_e2e_smoke_py}:{_line_of(loop_e2e_smoke_py_path, '_ROOT')}",
    }
    unexpected = [o for o in offenders if o not in permitted]

    assert unexpected == [], (
        "module-level parent-directory walk(s) found outside the permitted "
        f"single-hop case — these run at import time and can resolve to the "
        f"wrong location (or crash) in the image's shallow /app layout; "
        f"defer them into a function instead: {unexpected}"
    )
