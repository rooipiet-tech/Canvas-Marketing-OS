"""Every function package the orchestrator loads at runtime must be in the
image, prompt AND schema.

The container is built from services/orchestrator alone, so anything under
functions/ reaches it only by an explicit COPY. That has now bitten twice
in the same shape:

  * Prompts. The weekly loop added eight _read_prompt() call sites and a
    new package; none were staged, so every one of those handlers would
    have raised FileNotFoundError on the first real dispatch. The
    Dockerfile's own comment records it.

  * Schemas. schema.json stopped being documentation and became the wire
    format -- _validate_function_input / _validate_function_output load a
    package's own schema.json from functions/ at runtime. Function 41's
    was never staged when output validation was added to it, and would
    have failed identically on the next deploy.

The second is the first with a different file extension, so the guard is
the invariant rather than a list: a package worth staging a prompt for is
a package whose schema the orchestrator may validate against. Both the
Dockerfile and the image workflow are checked, because they stage the
same files by two different mechanisms and either can drift alone.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
DOCKERFILE = REPO_ROOT / "services/orchestrator/Dockerfile"
IMAGE_WORKFLOW = REPO_ROOT / ".github/workflows/orchestrator-image.yml"

# functions/_shared holds loose yaml, not packages, and has no schema.
NON_PACKAGE_PREFIXES = ("_shared",)


# The image workflow stages the eleven fan-out scanners through a shell
# loop -- `for pkg in 10-... 11-...; do cp "functions/$pkg/prompt.md"
# "functions/$pkg/schema.json"; done` -- so their paths never appear
# literally. Reading the loop's own package list is what keeps this guard
# comparing the same set on both sides rather than reporting a difference
# that is only in how the copy is spelled.
LOOP_PATTERN = re.compile(
    r"for pkg in\s+(?P<names>[0-9a-z][0-9a-z\-\s\\]*?);\s*do(?P<body>.*?)\bdone\b",
    re.DOTALL,
)


def _staged_packages(text: str, filename: str) -> set[str]:
    """Package directory names staged with `filename` in this file."""
    pattern = rf"functions/([0-9a-z][0-9a-z-]*)/{re.escape(filename)}"
    staged = set(re.findall(pattern, text))
    for match in LOOP_PATTERN.finditer(text):
        if f'functions/$pkg/{filename}' not in match.group("body"):
            continue
        staged.update(match.group("names").replace("\\", " ").split())
    return {name for name in staged if not name.startswith(NON_PACKAGE_PREFIXES)}


@pytest.mark.parametrize(
    "path", [DOCKERFILE, IMAGE_WORKFLOW], ids=["dockerfile", "image-workflow"]
)
def test_every_staged_prompt_has_its_schema_staged_too(path):
    text = path.read_text(encoding="utf-8")
    prompts = _staged_packages(text, "prompt.md")
    schemas = _staged_packages(text, "schema.json")

    assert prompts, f"no staged prompts found in {path.name} -- pattern drift?"
    missing = sorted(prompts - schemas)
    assert not missing, (
        f"{path.name} stages prompt.md but not schema.json for: {', '.join(missing)}. "
        "The orchestrator validates handler input and model output against a "
        "package's own schema.json at runtime, read from functions/ -- a package "
        "in the image without its schema raises on the first real dispatch."
    )


def test_the_two_staging_mechanisms_agree():
    """The Dockerfile and the image workflow stage the same files by
    different means. A package added to one and not the other is a build
    that works locally and fails in CI, or the reverse."""
    docker_text = DOCKERFILE.read_text(encoding="utf-8")
    workflow_text = IMAGE_WORKFLOW.read_text(encoding="utf-8")

    for filename in ("prompt.md", "schema.json"):
        in_docker = _staged_packages(docker_text, filename)
        in_workflow = _staged_packages(workflow_text, filename)
        assert in_docker == in_workflow, (
            f"{filename} staging differs: only in Dockerfile "
            f"{sorted(in_docker - in_workflow)}, only in workflow "
            f"{sorted(in_workflow - in_docker)}"
        )


def test_staged_files_actually_exist():
    """A COPY of a path that does not exist fails the build late and
    obscurely; catching a typo here is cheaper."""
    text = DOCKERFILE.read_text(encoding="utf-8")
    for filename in ("prompt.md", "schema.json"):
        for package in sorted(_staged_packages(text, filename)):
            assert (REPO_ROOT / "functions" / package / filename).is_file(), (
                f"Dockerfile stages functions/{package}/{filename}, which does not exist"
            )


def test_every_function_package_the_orchestrator_names_is_staged():
    """The gap the prompt/schema pairing above could not see.

    That check compares staged prompts against staged schemas, so it is
    blind to a package staged NEITHER way. Function 25 was exactly that:
    competitive_response_strategize_handler reads its prompt.md and
    validates against its schema.json at runtime, and it appeared in
    neither the Dockerfile nor the image workflow, because until that
    handler existed nothing had ever called it. In the deployed container
    that is a FileNotFoundError on the first real run.

    Derives the requirement from dispatch.py rather than a list: every
    FUNCTION_ID_* constant whose value names a real directory under
    functions/ is a package the orchestrator can load, and must therefore
    be in the image. Constants naming a policy key rather than a package
    (REAL_PUBLISH_FUNCTION_ID = "publish.social_post") are skipped by that
    same test, without needing an exemption list to maintain.
    """
    import re as _re

    dispatch_src = (REPO_ROOT / "services/orchestrator/orchestrator/dispatch.py").read_text(
        encoding="utf-8"
    )
    named = set(_re.findall(r'^[A-Z_]*FUNCTION_ID[A-Z_0-9]*\s*=\s*"([^"]+)"', dispatch_src, _re.M))
    named |= set(_re.findall(r'_read_prompt\(\s*"([^"]+)"', dispatch_src))
    packages = {
        name for name in named if (REPO_ROOT / "functions" / name / "prompt.md").is_file()
    }
    assert packages, "no function packages detected in dispatch.py -- pattern drift?"

    docker_text = DOCKERFILE.read_text(encoding="utf-8")
    workflow_text = IMAGE_WORKFLOW.read_text(encoding="utf-8")
    for filename in ("prompt.md", "schema.json"):
        staged = _staged_packages(docker_text, filename) & _staged_packages(
            workflow_text, filename
        )
        missing = sorted(packages - staged)
        assert not missing, (
            f"the orchestrator loads these packages at runtime but {filename} is not staged "
            f"in both the Dockerfile and the image workflow: {', '.join(missing)}"
        )


# --- every COPY'd path must be staged, _shared included ---------------------


def _dockerfile_shared_copies() -> list[str]:
    """Repo-root-relative `functions/_shared/...` paths the Dockerfile COPYs.

    Scoped to _shared on purpose. Function PACKAGES are already covered by
    the tests above, which understand that the eleven fan-out scanners are
    staged through a shell loop over function names rather than by literal
    path -- a substring check would report all of them as missing. _shared
    files are staged by literal `cp`, are the existing guard's explicit
    blind spot (NON_PACKAGE_PREFIXES), and are where the live break was.

    Only the source (first) argument matters: that is the path that must
    exist inside the build context by the time docker runs.
    """
    paths = []
    for line in DOCKERFILE.read_text().split("\n"):
        stripped = line.strip()
        if not stripped.startswith("COPY "):
            continue
        parts = stripped.split()
        if len(parts) >= 2 and parts[1].startswith("functions/_shared/"):
            paths.append(parts[1])
    return sorted(set(paths))


def test_the_dockerfile_copies_shared_files_at_all():
    """Guard the guard: no COPYs found would make the test below vacuous."""
    assert _dockerfile_shared_copies(), (
        f"no `COPY functions/_shared/...` lines found in {DOCKERFILE} -- the "
        "staging mechanism this test checks has changed shape"
    )


@pytest.mark.parametrize("copied", _dockerfile_shared_copies())
def test_every_shared_file_the_dockerfile_copies_is_staged(copied: str):
    """F-SCORING-POLICY-UNSTAGED (live, deploy-pipeline run 3).

    The image is built from services/orchestrator alone, so anything under
    functions/ -- a sibling of services/ at the repo root -- reaches the
    build context only via an explicit `cp` in orchestrator-image.yml. A
    Dockerfile COPY without a matching staging step is not a runtime
    FileNotFoundError; it fails the BUILD:

        ERROR: failed to compute cache key: failed to calculate checksum
        of ref ...: "/functions/_shared/scoring-policy.yaml": not found

    PR #123 added functions/_shared/scoring-policy.yaml and its COPY line
    without the `cp`, which broke stage 3 of the deploy pipeline and, with
    the stages sequential, everything after it.

    The pre-existing guard above could not catch it: it reasons about
    function PACKAGES and deliberately excludes functions/_shared, which
    is exactly where the new file lives. This one asserts the invariant
    that actually matters and has no such blind spot -- whatever the
    Dockerfile COPYs, the workflow must stage.
    """
    staged = IMAGE_WORKFLOW.read_text()
    assert copied in staged, (
        f"{DOCKERFILE.name} has `COPY {copied}` but {IMAGE_WORKFLOW.name} never "
        f"stages it into the build context. docker cannot reach outside "
        f"services/orchestrator, so this fails the image build itself with "
        f'"{copied}: not found" -- and with the deploy pipeline sequential, every '
        "stage after the orchestrator image with it."
    )
