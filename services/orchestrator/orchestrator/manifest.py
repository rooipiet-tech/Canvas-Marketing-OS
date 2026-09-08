"""TD-09 (docs/architecture/09-technical-debt.md): verify the registry's
signed manifest at orchestrator startup, and resolve every prompt.md
THROUGH it rather than trusting orchestrator.config.functions_dir() to hand
back an arbitrary file's bytes unchecked.

BEFORE THIS MODULE: services/registry/build_registry.py produced a signed,
reproducible manifest and services/registry/verify_signature.py could check
it -- but nothing at runtime ever called verify_signature.py, and
dispatch.py's _read_prompt() read functions/<id>/prompt.md straight off
disk. A prompt.md modified inside the built container image (a compromised
base image, a bad COPY, a supply-chain tamper anywhere between "the
manifest was signed" and "the container is running") would run exactly as
happily as the real one. The signed manifest was theatre.

THE FIX. load_verified_manifest() verifies the manifest's Ed25519 signature
(reusing services/registry/signing.py's own verify_bytes()/key-resolution
order, dynamically loaded rather than reimplemented -- L-0013, a
shared-mechanism fix is a bug-class fix, applies just as much to reuse as
to repair) and caches the result for the life of the process. resolve_prompt()
is what dispatch.py's _read_prompt() now calls instead of reading
functions_dir() directly: it looks up the per-file SHA-256 the manifest
recorded for functions/<id>/prompt.md (build_registry.py's new "files" map,
added alongside this module since the orchestrator image stages only
prompt.md/schema.json per package, never the whole package the existing
package-level content_sha256 covers), recomputes that same hash from the
file actually on disk, and refuses to return the content on any mismatch.

HARD-FAIL BY DESIGN, UNLIKE EVERY OTHER config.py-ADJACENT INTEGRATION.
DATABASE_URL/SERVICE_BUS_NAMESPACE/VAULT_API_URL/TEAMS_WEBHOOK_URL/App
Insights all degrade to a WARNING and keep serving (main.py's lifespan,
config.py's own module docstring) -- that is correct for an optional
infrastructure dependency that is legitimately absent in local dev and CI.
A registry manifest that cannot be verified is not an absent integration;
it is a supply-chain integrity failure, so ManifestVerificationError is
allowed to propagate all the way out of main.py's lifespan, uncaught,
crashing FastAPI startup instead of logging a line and serving whatever is
on disk. The one exception is telemetry_wiring.py's registry_version span
attribute (also named by TD-09 as unpopulated) -- tagging a span is
inherently best-effort, so that one caller catches
ManifestVerificationError and falls back to the same honest
"unversioned" placeholder it always used, exactly like configure_tracer()'s
own "telemetry must never crash" contract.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import logging
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

from orchestrator import config
from orchestrator.logging_config import get_logger, log_event

logger = get_logger("manifest")

REGISTRY_FILENAME = "registry.json"
SIGNATURE_FILENAME = "registry.json.sig"

# Registered in sys.modules under services/registry/signing.py's own
# unqualified module name ("signing") is deliberately avoided -- dispatch.py
# already dynamically loads a different file under a namespaced key
# (load_permission_check()'s _PERMISSION_CHECK_MODULE_NAME) for the exact
# same reason: a generic name is one a future import elsewhere in this
# process could collide with. "common" cannot be namespaced the same way
# (see _load_registry_tooling()'s docstring for why), so it carries its own
# explicit collision guard instead.
_SIGNING_MODULE_NAME = "cmos_orchestrator_registry_signing"
_COMMON_MODULE_NAME = "common"


class ManifestVerificationError(RuntimeError):
    """The registry manifest could not be loaded and verified.

    Every raise site in this module is a hard-fail condition on purpose --
    see this module's own docstring for why a supply-chain integrity
    failure is not treated like an absent optional integration."""


def _load_module_from_path(module_name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ManifestVerificationError(f"cannot load {path} as a Python module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


def _load_registry_tooling(tooling_dir: Path | None) -> tuple[ModuleType, ModuleType]:
    """Dynamically loads services/registry's own common.py + signing.py.

    services/registry is a plain, directly-runnable script directory (see
    that package's common.py module docstring), not an installed package --
    each of its scripts imports its siblings with a bare `from common
    import ...` / `from signing import ...`, which only resolves because
    running `python services/registry/foo.py` puts that script's own
    directory on sys.path. This service does the same thing dispatch.py's
    load_permission_check() already does for
    functions/02-brand-steward-qa/permission_check.py: load the file
    directly from its path via importlib rather than requiring
    services/registry to become an installed dependency of this service.

    common.py MUST be registered under its own real name ("common") before
    signing.py is loaded, because signing.py's own top-level `from common
    import REGISTRY_DIR, fail, rel_or_str, warn` resolves through
    sys.modules exactly as it does when services/registry's scripts run
    directly -- there is no way to rename that import without editing
    signing.py itself. Since "common" is a generic name a different
    dependency could plausibly also claim, an already-registered "common"
    that is not THIS module (checked by its __file__) is refused rather
    than silently shadowed or silently reused.
    """
    tooling_dir = tooling_dir or config.registry_tooling_dir()
    common_path = tooling_dir / "common.py"
    signing_path = tooling_dir / "signing.py"
    if not common_path.is_file() or not signing_path.is_file():
        raise ManifestVerificationError(
            f"registry verification code not found under {tooling_dir} "
            "(expected common.py and signing.py) -- REGISTRY_TOOLING_DIR misconfigured?"
        )

    existing_common = sys.modules.get(_COMMON_MODULE_NAME)
    if existing_common is not None and getattr(existing_common, "__file__", None) != str(
        common_path
    ):
        raise ManifestVerificationError(
            f"a module named {_COMMON_MODULE_NAME!r} is already loaded from "
            f"{getattr(existing_common, '__file__', '<unknown>')}, not {common_path} -- "
            "refusing to shadow it with services/registry/common.py (call "
            "orchestrator.manifest.reset_manifest_cache() before pointing "
            "REGISTRY_TOOLING_DIR elsewhere mid-process)"
        )
    common_module = existing_common or _load_module_from_path(_COMMON_MODULE_NAME, common_path)

    signing_module = sys.modules.get(_SIGNING_MODULE_NAME)
    if signing_module is not None and getattr(signing_module, "__file__", None) != str(
        signing_path
    ):
        raise ManifestVerificationError(
            f"a module named {_SIGNING_MODULE_NAME!r} is already loaded from "
            f"{getattr(signing_module, '__file__', '<unknown>')}, not {signing_path} -- "
            "call orchestrator.manifest.reset_manifest_cache() before pointing "
            "REGISTRY_TOOLING_DIR elsewhere mid-process"
        )
    if signing_module is None:
        signing_module = _load_module_from_path(_SIGNING_MODULE_NAME, signing_path)
    return common_module, signing_module


def _call_capturing_fail(fn, *args, **kwargs):
    """Calls a services/registry helper that fails loudly via common.py's
    own fail() (prints "FAIL: <reason>" to stderr, sys.exit(1)) and
    converts that CLI-style exit into a ManifestVerificationError carrying
    the same message.

    Reusing signing.py's actual key-resolution/verification functions
    as-is (rather than re-deriving their logic here) is the point of this
    module -- see the module docstring's L-0013 citation -- but a bare
    SystemExit is awkward to assert against from a FastAPI lifespan or a
    test, and BaseException (SystemExit's own base) would slip past an
    ordinary `except Exception`, exactly the failure mode this module's own
    docstring says must NOT happen silently.
    """
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stderr(buffer):
            return fn(*args, **kwargs)
    except SystemExit as exc:
        fn_name = getattr(fn, "__name__", fn)
        message = buffer.getvalue().strip() or f"{fn_name} failed (exit {exc.code})"
        raise ManifestVerificationError(message) from exc


@dataclass(frozen=True)
class VerifiedManifest:
    """The registry manifest, already signature-verified by the time this
    object exists -- there is no code path that constructs one otherwise."""

    tag: str
    registry_schema_version: int
    function_count: int
    key_fingerprint: str
    files_by_function: dict[str, dict[str, str]] = field(default_factory=dict)

    def has_function(self, function_id: str) -> bool:
        return function_id in self.files_by_function

    def file_sha256(self, function_id: str, relative_path: str) -> str | None:
        return self.files_by_function.get(function_id, {}).get(relative_path)


def load_verified_manifest(
    *, manifest_dir: Path | None = None, tooling_dir: Path | None = None
) -> VerifiedManifest:
    """Reads, signature-verifies, and parses the registry manifest. Always
    either returns a VerifiedManifest whose signature genuinely checked out,
    or raises ManifestVerificationError -- never a partially-trusted result.
    """
    manifest_dir = manifest_dir or config.registry_manifest_dir()
    registry_path = manifest_dir / REGISTRY_FILENAME
    signature_path = manifest_dir / SIGNATURE_FILENAME

    if not registry_path.is_file():
        raise ManifestVerificationError(
            f"registry manifest not found at {registry_path} -- build it with "
            "`python services/registry/build_registry.py --out "
            f"{manifest_dir} --sign` (TD-09; CI builds this before the "
            "orchestrator test suite/image runs, see .github/workflows/ci.yml "
            "and orchestrator-image.yml)"
        )
    if not signature_path.is_file():
        raise ManifestVerificationError(
            f"detached signature not found at {signature_path} -- build with --sign"
        )

    _common, signing = _load_registry_tooling(tooling_dir)

    payload = registry_path.read_bytes()
    signature_text = signature_path.read_text(encoding="utf-8")

    public_key_path = _call_capturing_fail(signing.resolve_public_key_path)

    if not signing.verify_bytes(payload, signature_text, public_key_path):
        raise ManifestVerificationError(
            f"{signing.SIGNATURE_ALGORITHM} signature verification FAILED for "
            f"{registry_path} against key {public_key_path} -- the manifest content "
            "and the signature do not match (content tampered, signature tampered, "
            "or a different signing key was used)"
        )

    try:
        manifest = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestVerificationError(
            f"{registry_path} verified but is not valid JSON: {exc}"
        ) from exc

    # "functions" is the full-shape package list (prompt.md, skill.md,
    # tools.yaml, schema.json, evals/ -- AC-01/AC-14/AC-27). "scaffold_functions"
    # (build_registry.py's discover_scaffold_packages()) additively covers
    # every OTHER functions/ directory that carries at least a prompt.md --
    # several of dispatch.py's real handlers read exactly one of these at
    # runtime (e.g. Fn 124/125's own "status: scaffold" packages), so their
    # prompt.md needs a recorded hash here too, or resolve_prompt() below
    # would refuse to run something that runs today.
    files_by_function: dict[str, dict[str, str]] = {}
    for list_key in ("functions", "scaffold_functions"):
        for entry in manifest.get(list_key, []):
            function_id = entry.get("id")
            files = entry.get("files")
            if not function_id or not isinstance(files, dict):
                raise ManifestVerificationError(
                    f"{registry_path} verified but has a malformed entry in "
                    f"{list_key!r} (missing id/files): {entry!r}"
                )
            if function_id in files_by_function:
                raise ManifestVerificationError(
                    f"{registry_path} lists {function_id!r} in both 'functions' "
                    "and 'scaffold_functions'"
                )
            files_by_function[function_id] = files

    fingerprint = _call_capturing_fail(signing.public_key_fingerprint, public_key_path)

    verified = VerifiedManifest(
        tag=str(manifest.get("tag", "unknown")),
        registry_schema_version=int(manifest.get("registry_schema_version", 0)),
        function_count=int(manifest.get("function_count", len(files_by_function))),
        key_fingerprint=fingerprint,
        files_by_function=files_by_function,
    )
    log_event(
        logger,
        logging.INFO,
        "registry_manifest_verified",
        tag=verified.tag,
        function_count=verified.function_count,
        key_fingerprint=verified.key_fingerprint,
    )
    return verified


_cache_lock = threading.Lock()
_cached_manifest: VerifiedManifest | None = None


def get_manifest(*, force_reload: bool = False) -> VerifiedManifest:
    """The process-wide verified manifest -- loaded and signature-verified
    once, then cached. main.py's lifespan calls this first, outside any
    try/except, so an unverifiable manifest crashes startup instead of
    logging a WARNING and continuing (see this module's own docstring for
    why the registry manifest is not like DB/Service Bus/Vault). Every
    later caller -- dispatch.py's _read_prompt(), telemetry_wiring.py's
    registry_version default -- reuses this SAME cached, already-verified
    manifest rather than re-verifying the Ed25519 signature on every
    dispatch.
    """
    global _cached_manifest
    with _cache_lock:
        if _cached_manifest is None or force_reload:
            _cached_manifest = load_verified_manifest()
        return _cached_manifest


def reset_manifest_cache() -> None:
    """Test-only: clears the cached manifest and any dynamically loaded
    registry-tooling modules, so a test can point REGISTRY_MANIFEST_DIR/
    REGISTRY_TOOLING_DIR at a fresh fixture and re-verify from scratch."""
    global _cached_manifest
    with _cache_lock:
        _cached_manifest = None
    sys.modules.pop(_SIGNING_MODULE_NAME, None)
    sys.modules.pop(_COMMON_MODULE_NAME, None)


def resolve_prompt(function_id: str, *, functions_dir: Path | None = None) -> str:
    """Reads functions/<function_id>/prompt.md THROUGH the verified
    registry manifest -- what dispatch.py's _read_prompt() calls instead of
    reading functions_dir() directly (TD-09).

    The prompt's own bytes still come from disk (the manifest records
    hashes, not content -- see build_registry.py's file_hashes()), but the
    manifest is what AUTHORISES returning them: this function looks up the
    SHA-256 the signed manifest recorded for this function's prompt.md,
    recomputes the identical hash (LF-normalised, via the SAME common.py
    helpers build_registry.py itself uses -- never re-derived) from the
    file actually present under functions_dir(), and raises rather than
    returns on any mismatch, any unregistered function_id, or any entry
    missing a recorded hash. A prompt.md modified after the manifest was
    signed -- the exact "theatre" TD-09 named -- fails here instead of
    running.
    """
    manifest = get_manifest()
    if not manifest.has_function(function_id):
        raise ManifestVerificationError(
            f"{function_id!r} is not a package the signed registry manifest knows "
            "about -- refusing to run an unregistered function's prompt"
        )
    expected = manifest.file_sha256(function_id, "prompt.md")
    if expected is None:
        raise ManifestVerificationError(
            "the signed registry manifest has no prompt.md hash recorded for "
            f"{function_id!r} -- was the manifest built before this function's "
            "prompt existed?"
        )

    prompt_path = (functions_dir or config.functions_dir()) / function_id / "prompt.md"
    if not prompt_path.is_file():
        raise ManifestVerificationError(
            f"{prompt_path} is recorded in the signed manifest but missing on disk"
        )

    common_module, _signing = _load_registry_tooling(None)
    actual = common_module.sha256_bytes(common_module.normalise_text_bytes(prompt_path))
    if actual != expected:
        raise ManifestVerificationError(
            f"prompt.md for {function_id!r} does not match the signed registry "
            f"manifest (expected sha256 {expected}, got {actual}) -- refusing to "
            "run a tampered prompt"
        )
    return prompt_path.read_text(encoding="utf-8")
