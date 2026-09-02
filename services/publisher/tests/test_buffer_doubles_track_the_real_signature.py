"""A1 (2 Sep 2026) — every BufferClient double accepts what the real
client accepts.

THE BUG THIS EXISTS BECAUSE OF. A1 added two optional attribution labels
to `BufferClient.create_draft` (utm_campaign, post_archetype) and updated
the one production call site. Five test doubles across four files still
declared `def create_draft(self, *, channel_id, text)`. Two of them were
on paths the publish router actually walks, so they raised

    TypeError: create_draft() got an unexpected keyword argument
               'utm_campaign'

A double that accepts FEWER keyword arguments than the thing it stands in
for does not fail safe. It raises on a call the real client handles fine
— a fake failure that looks like a real one, or worse, one nobody sees.

It went unseen for a different reason worth recording here: no workflow
in .github/workflows runs the publisher test suite. ci.yml has jobs for
the orchestrator, gatekeeper, analytics-ingest, telemetry-lib and
console, and installs services/publisher/requirements.txt only to verify
the governance bundle. So PR #129 was green in CI and broken in fact.

CLAUDE.md's hard rule 10 is the general form: patching one call site of a
shared helper reliably leaves siblings broken, so audit every call site.
This test is that audit, made permanent — the next parameter added to
create_draft fails here immediately rather than in whichever double is
unlucky.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from app.buffer_client import BufferClient

TESTS_DIR = Path(__file__).resolve().parent


def _real_keyword_parameters() -> set[str]:
    signature = inspect.signature(BufferClient.create_draft)
    return {
        name
        for name, parameter in signature.parameters.items()
        if parameter.kind is inspect.Parameter.KEYWORD_ONLY
    }


def _double_create_drafts() -> list[tuple[Path, ast.FunctionDef]]:
    """Every `def create_draft` defined anywhere in this test suite."""
    found: list[tuple[Path, ast.FunctionDef]] = []
    for path in sorted(TESTS_DIR.glob("test_*.py")):
        if path.name == Path(__file__).name:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "create_draft":
                found.append((path, node))
    return found


def test_the_suite_actually_contains_doubles_to_check() -> None:
    """Guards the vacuous pass. If the discovery above ever stops finding
    anything — a rename, a move — this test must fail rather than quietly
    assert nothing, which is the failure mode CLAUDE.md's own convention
    on verify commands warns about (L-0005, L-0046, L-0059)."""
    assert len(_double_create_drafts()) >= 4


def test_every_double_accepts_every_keyword_the_real_client_takes() -> None:
    real = _real_keyword_parameters()
    assert "channel_id" in real and "text" in real, (
        "the real client's shape changed beyond what this guard understands"
    )

    failures: list[str] = []
    for path, node in _double_create_drafts():
        double_kwargs = {arg.arg for arg in node.args.kwonlyargs}
        if node.args.kwarg is not None:
            continue  # **kwargs absorbs anything; nothing to drift
        missing = real - double_kwargs
        if missing:
            failures.append(
                f"{path.name}:{node.lineno} create_draft is missing "
                f"{sorted(missing)} — the real BufferClient.create_draft takes them"
            )

    assert not failures, "\n".join(failures)


def test_the_extra_labels_are_optional_on_the_real_client() -> None:
    """The property that lets a double default them to None safely, and
    the one that matters in production: a post whose asset carries no
    archetype must still publish. A reporting label may never be able to
    refuse a publish."""
    signature = inspect.signature(BufferClient.create_draft)
    for name in ("utm_campaign", "post_archetype"):
        assert signature.parameters[name].default is None
    for name in ("channel_id", "text"):
        assert signature.parameters[name].default is inspect.Parameter.empty
