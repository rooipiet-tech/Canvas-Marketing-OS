"""Shared helpers for the function-definition registry tooling.

Every script under `services/registry/` is a plain, directly-runnable
Python program (`python services/registry/<script>.py [args]`) that prints
a final `PASS` line on stdout on success, or a `FAIL: <reason>` line on
stderr and exits non-zero on failure. This mirrors the house style already
set by `scripts/validate_contracts.py`.

Nothing in here reads the clock, the environment's working directory, or
any absolute path into an output artefact — registry builds must be
byte-identical across machines and runs (AC-02/AC-23).
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, NoReturn


def _force_utf8_streams() -> None:
    """Make stdout/stderr UTF-8 regardless of the host console codepage.

    Windows consoles default to a legacy codepage, which mangles the em
    dashes and non-ASCII text in these scripts' diagnostics. CI output and
    local output should read identically.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):  # pragma: no cover - detached/odd streams
            pass


_force_utf8_streams()

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
REGISTRY_DIR = REPO_ROOT / "services" / "registry"
FUNCTIONS_DIR = REPO_ROOT / "functions"
CONTRACTS_DIR = REPO_ROOT / "contracts"

TOOLS_SCHEMA_PATH = CONTRACTS_DIR / "function-definition" / "tools.schema.json"
EVAL_TASK_SCHEMA_PATH = REGISTRY_DIR / "eval_task.schema.json"
PERMISSION_REGISTER_PATH = REPO_ROOT / "docs" / "permission-register.yaml"

# The file/directory shape every function-definition package must have,
# mirroring contracts/function-definition/TEMPLATE/.
REQUIRED_PACKAGE_FILES = ("prompt.md", "skill.md", "tools.yaml", "schema.json")
REQUIRED_PACKAGE_DIRS = ("evals",)

# Repo-root marker used when walking up from a nested file (function
# packages sit three or more levels below the root).
REPO_ROOT_MARKER = "pyproject.toml"


def fail(message: str) -> NoReturn:
    """Print a machine-parseable failure line and exit non-zero."""
    print(f"FAIL: {message}", file=sys.stderr)
    sys.exit(1)


def warn(message: str) -> None:
    """Print a warning to stderr without affecting the exit code."""
    print(f"WARNING: {message}", file=sys.stderr)


def find_repo_root(start: Path) -> Path:
    """Walk up from `start` until a directory containing the repo-root marker.

    Generalises `scripts/validate_contracts.py`'s `Path(__file__).resolve()
    .parent.parent` idiom for files nested at unknown depth (e.g. a function
    package's own helper module). Never uses the current working directory.
    """
    current = start.resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / REPO_ROOT_MARKER).is_file():
            return candidate
    raise RuntimeError(
        f"could not locate repo root (no {REPO_ROOT_MARKER}) walking up from {start}"
    )


def canonical_json(payload: Any) -> str:
    """Serialise to canonical JSON: sorted keys, fixed separators, trailing newline.

    Deterministic by construction — no timestamps, no insertion-order
    dependence, no platform-dependent float formatting beyond Python's own
    repr, which is stable across the supported interpreters.
    """
    return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def normalise_text_bytes(path: Path) -> bytes:
    """Read a file as bytes with line endings normalised to LF.

    Git may check text files out with CRLF on Windows; hashing the raw bytes
    would then produce a different content hash on Windows than on the
    ubuntu-latest CI runner for the same commit. Normalising to LF makes the
    per-function content hash a property of the commit, not the checkout.
    """
    return path.read_bytes().replace(b"\r\n", b"\n")


def rel_posix(path: Path, root: Path) -> str:
    """Path relative to `root`, forward slashes — never an absolute path.

    Falls back to the path's own last two segments when it lies outside
    `root` (as it does when `--functions-dir` points at a scratch copy).
    An absolute path must never reach the manifest: it would embed the
    build machine's layout and break byte-identical reproducibility.
    """
    resolved = path.resolve()
    try:
        return resolved.relative_to(root.resolve()).as_posix()
    except ValueError:
        return Path(resolved.parent.name, resolved.name).as_posix()


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(f"{rel_or_str(path)}: invalid JSON ({exc})")


def load_yaml(path: Path) -> Any:
    import yaml

    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        fail(f"{rel_or_str(path)}: invalid YAML ({exc})")


def rel_or_str(path: Path) -> str:
    """Best-effort repo-relative display path, falling back to the raw path."""
    try:
        return rel_posix(Path(path), REPO_ROOT)
    except ValueError:
        return str(path)


def iter_package_files(package_dir: Path) -> list[Path]:
    """All content files of a package, in a stable sorted order.

    Deliberately excludes caches and compiled artefacts so the content hash
    covers only committed source.
    """
    skip_dirs = {"__pycache__", ".pytest_cache", ".ruff_cache"}
    files: list[Path] = []
    for candidate in package_dir.rglob("*"):
        if not candidate.is_file():
            continue
        if any(part in skip_dirs for part in candidate.parts):
            continue
        if candidate.suffix in {".pyc", ".pyo"}:
            continue
        files.append(candidate)
    return sorted(files, key=lambda p: p.relative_to(package_dir).as_posix())


def is_function_package(path: Path) -> bool:
    """True when `path` has the full TEMPLATE package shape."""
    if not path.is_dir():
        return False
    if not all((path / name).is_file() for name in REQUIRED_PACKAGE_FILES):
        return False
    return all((path / name).is_dir() for name in REQUIRED_PACKAGE_DIRS)


def discover_function_packages(functions_dir: Path | None = None) -> list[Path]:
    """Every function-definition package under functions/, sorted by directory name.

    Sibling directories that are not function-definition packages (e.g.
    `functions/task-worker/`, the Azure Function App scaffold) are skipped
    rather than rejected — per SCOPE-001 they coexist as siblings.
    """
    root = functions_dir or FUNCTIONS_DIR
    if not root.is_dir():
        return []
    return sorted(
        (child for child in root.iterdir() if is_function_package(child)),
        key=lambda p: p.name,
    )


def eval_task_files(package_dir: Path) -> list[Path]:
    """Golden eval task files for a package, sorted by filename."""
    evals_dir = package_dir / "evals"
    if not evals_dir.is_dir():
        return []
    return sorted(evals_dir.glob("*.json"), key=lambda p: p.name)
