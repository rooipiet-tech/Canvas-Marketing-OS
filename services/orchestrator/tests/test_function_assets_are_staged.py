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
