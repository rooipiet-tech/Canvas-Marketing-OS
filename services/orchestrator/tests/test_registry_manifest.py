"""TD-09 (docs/architecture/09-technical-debt.md): the registry's signed
manifest must actually be verified at runtime, and dispatch.py's prompts
must resolve THROUGH that verification rather than around it.

Every fixture manifest here is built with the REAL
services/registry/build_registry.py --sign (via subprocess, against a
throwaway functions_dir), never a hand-rolled JSON blob -- this exercises
the exact artefact shape and signing path CI and the orchestrator image
build actually produce, not a stand-in for it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from orchestrator import manifest

REPO_ROOT = Path(__file__).resolve().parents[3]
BUILD_REGISTRY = REPO_ROOT / "services" / "registry" / "build_registry.py"


@pytest.fixture(autouse=True)
def _reset_manifest_cache():
    """orchestrator.manifest caches the verified manifest process-wide
    (deliberately -- see get_manifest()'s own docstring) and dynamically
    loads services/registry's common.py/signing.py into sys.modules the
    same way. Both must be cleared before AND after every test in this
    file, or a manifest verified against one test's fixture directory
    would leak into the next test (or into a completely different test
    file that also calls _read_prompt / get_manifest)."""
    manifest.reset_manifest_cache()
    yield
    manifest.reset_manifest_cache()


def _build_signed_manifest(functions_dir: Path, out_dir: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(BUILD_REGISTRY),
            "--functions-dir",
            str(functions_dir),
            "--out",
            str(out_dir),
            "--sign",
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, (
        f"build_registry.py --sign failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )


def _write_fixture_package(functions_dir: Path, function_id: str, prompt_text: str) -> Path:
    """A minimal "scaffold" package (prompt.md only) -- deliberately not
    the full AC-01/AC-14/AC-27 shape, mirroring the several real
    functions/ packages (Fn 113-127) dispatch.py already reads that are
    exactly this shape -- see build_registry.py's discover_scaffold_packages()."""
    package_dir = functions_dir / function_id
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / "prompt.md").write_text(prompt_text, encoding="utf-8")
    return package_dir


def _point_env_at_fixture(monkeypatch, functions_dir: Path, manifest_dir: Path) -> None:
    monkeypatch.setenv("FUNCTIONS_DIR", str(functions_dir))
    monkeypatch.setenv("REGISTRY_MANIFEST_DIR", str(manifest_dir))


# ---------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------


def test_a_validly_signed_manifest_verifies_and_resolves_its_prompt(tmp_path, monkeypatch):
    functions_dir = tmp_path / "functions"
    _write_fixture_package(functions_dir, "99-fixture-function", "You are a fixture agent.\n")
    manifest_dir = tmp_path / "dist"
    _build_signed_manifest(functions_dir, manifest_dir)
    _point_env_at_fixture(monkeypatch, functions_dir, manifest_dir)

    verified = manifest.get_manifest()

    assert verified.tag
    assert verified.has_function("99-fixture-function")
    assert manifest.resolve_prompt("99-fixture-function") == "You are a fixture agent.\n"


def test_the_cached_manifest_is_not_reverified_on_every_call(tmp_path, monkeypatch):
    """get_manifest()'s whole point is to verify the Ed25519 signature
    once per process, not once per dispatch."""
    functions_dir = tmp_path / "functions"
    _write_fixture_package(functions_dir, "99-fixture-function", "prompt\n")
    manifest_dir = tmp_path / "dist"
    _build_signed_manifest(functions_dir, manifest_dir)
    _point_env_at_fixture(monkeypatch, functions_dir, manifest_dir)

    first = manifest.get_manifest()
    second = manifest.get_manifest()

    assert first is second


# ---------------------------------------------------------------------
# Tampering after the manifest was signed
# ---------------------------------------------------------------------


def test_a_prompt_modified_after_signing_fails_loudly_instead_of_running(tmp_path, monkeypatch):
    """The register's own words (TD-09): 'A modified prompt.md in the
    container image runs happily' -- must no longer be true."""
    functions_dir = tmp_path / "functions"
    _write_fixture_package(functions_dir, "99-fixture-function", "original prompt\n")
    manifest_dir = tmp_path / "dist"
    _build_signed_manifest(functions_dir, manifest_dir)
    _point_env_at_fixture(monkeypatch, functions_dir, manifest_dir)

    # Tamper with the file ON DISK after the manifest was built and signed
    # -- exactly the "modified prompt.md in the container image" scenario.
    (functions_dir / "99-fixture-function" / "prompt.md").write_text(
        "a tampered prompt that never went through registry signing\n", encoding="utf-8"
    )

    with pytest.raises(manifest.ManifestVerificationError, match="does not match"):
        manifest.resolve_prompt("99-fixture-function")


def test_an_unregistered_function_id_is_refused_not_read(tmp_path, monkeypatch):
    functions_dir = tmp_path / "functions"
    _write_fixture_package(functions_dir, "99-fixture-function", "prompt\n")
    manifest_dir = tmp_path / "dist"
    _build_signed_manifest(functions_dir, manifest_dir)
    _point_env_at_fixture(monkeypatch, functions_dir, manifest_dir)

    # A directory dropped onto disk after the manifest was built and
    # signed (e.g. added to the image out of band) -- it never appears in
    # the signed manifest's per-function file map either way.
    _write_fixture_package(functions_dir, "00-never-registered", "not signed\n")

    with pytest.raises(manifest.ManifestVerificationError, match="not a package"):
        manifest.resolve_prompt("00-never-registered")


def test_a_missing_manifest_fails_loudly_not_silently(tmp_path, monkeypatch):
    monkeypatch.setenv("REGISTRY_MANIFEST_DIR", str(tmp_path / "does-not-exist"))

    with pytest.raises(manifest.ManifestVerificationError, match="registry manifest not found"):
        manifest.get_manifest()


def test_a_missing_signature_file_fails_loudly_not_silently(tmp_path, monkeypatch):
    functions_dir = tmp_path / "functions"
    _write_fixture_package(functions_dir, "99-fixture-function", "prompt\n")
    manifest_dir = tmp_path / "dist"
    _build_signed_manifest(functions_dir, manifest_dir)
    (manifest_dir / "registry.json.sig").unlink()
    _point_env_at_fixture(monkeypatch, functions_dir, manifest_dir)

    with pytest.raises(manifest.ManifestVerificationError, match="detached signature not found"):
        manifest.get_manifest()


# ---------------------------------------------------------------------
# TD-09's actual acceptance bar: startup itself must crash
# ---------------------------------------------------------------------


def test_a_manifest_whose_signature_no_longer_matches_fails_orchestrator_startup_loudly(
    tmp_path, monkeypatch
):
    """Unlike every other config.py-adjacent integration this service has
    (DATABASE_URL/SERVICE_BUS_NAMESPACE/VAULT_API_URL/TEAMS_WEBHOOK_URL/App
    Insights all degrade to a WARNING and keep serving -- main.py's
    lifespan, config.py's own module docstring), a registry manifest whose
    signature does not verify must crash FastAPI startup outright. This is
    TD-09's actual acceptance bar, not just resolve_prompt()'s: 'fails
    orchestrator startup loudly rather than running silently'.
    """
    functions_dir = tmp_path / "functions"
    _write_fixture_package(functions_dir, "99-fixture-function", "prompt\n")
    manifest_dir = tmp_path / "dist"
    _build_signed_manifest(functions_dir, manifest_dir)

    # Corrupt the manifest AFTER it was signed -- content and signature no
    # longer agree, indistinguishable from a real tamper in transit/at
    # rest.
    registry_json = manifest_dir / "registry.json"
    registry_json.write_bytes(registry_json.read_bytes() + b"x")

    _point_env_at_fixture(monkeypatch, functions_dir, manifest_dir)
    monkeypatch.setenv("VAULT_API_URL", "http://127.0.0.1:1")

    import main as orchestrator_main
    from fastapi.testclient import TestClient

    with pytest.raises(manifest.ManifestVerificationError, match="signature verification FAILED"):
        with TestClient(orchestrator_main.app):
            pass
