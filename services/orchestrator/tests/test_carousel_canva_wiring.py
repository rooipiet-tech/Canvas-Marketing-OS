"""A3 (2 Sep 2026) — function 45's Canva manifest finally reaches Canva.

mcp-canva shipped with three template-locked tools, its own managed
identity, Key Vault role, ACR role, Container App and a smoke job, and
nothing in this system ever called it. Function 45 produced a Canva Bulk
Create CSV manifest, validated its own shape locally, and the manifest
stopped there — someone had to paste it into Canva by hand.

These tests pin the wiring and, more importantly, the four properties
that keep it from making the system worse than the version that never
called Canva at all:

  1. The template id is lifted OUT of the CSV rows. It is a column in the
     manifest only because a flat CSV has nowhere else to put it; for
     Canva it is a job-level concern.
  2. A manifest whose rows disagree about the template generates nothing.
     That is one malformed deck, not two decks.
  3. Dry-run is the DEFAULT, and in dry-run no client is constructed at
     all. This matters because canva-refresh-token is not populated, so a
     call today cannot succeed.
  4. A Canva failure never fails the drafting task. Thursday's QA reviews
     the slide copy, not the deck, and the manifest is still in the draft
     text for manual bulk-create.
"""

from __future__ import annotations

import logging

import pytest
from orchestrator import dispatch
from orchestrator.clients.mcp_client import MCPClientError

MANIFEST = (
    "slide_number,headline,subhead,image_ref,brand_template_id\n"
    "1,Month-end two days sooner,Finance-grade trust,slide-1.png,TPL-CAROUSEL-1\n"
    "2,One governed lakehouse,Consolidation at scale,slide-2.png,TPL-CAROUSEL-1\n"
    "3,Your Data. Delivered.,,slide-3.png,TPL-CAROUSEL-1\n"
)


class _FakeCanvaClient:
    def __init__(self, calls: list, result=None, raises=None):
        self._calls = calls
        self._result = result or {"source": "live", "result": {"designs_requested": 3}}
        self._raises = raises

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def call_tool(self, tool_name, arguments):
        self._calls.append((tool_name, arguments))
        if self._raises is not None:
            raise self._raises
        return self._result


# ---------------------------------------------------------------------
# Manifest parsing
# ---------------------------------------------------------------------

def test_the_template_id_is_lifted_out_of_the_rows():
    template_id, rows = dispatch._parse_canva_manifest(MANIFEST)
    assert template_id == "TPL-CAROUSEL-1"
    assert len(rows) == 3
    assert rows[0]["headline"] == "Month-end two days sooner"
    # It stays on the rows here; stripping it is mcp-canva's job, because
    # that is where the autofill `data` object is actually built and where
    # the dataset says which field names exist.
    assert rows[0]["brand_template_id"] == "TPL-CAROUSEL-1"


def test_rows_disagreeing_about_the_template_generate_nothing():
    mixed = MANIFEST.replace("TPL-CAROUSEL-1\n3,", "TPL-DIFFERENT\n3,")
    template_id, rows = dispatch._parse_canva_manifest(mixed)
    assert template_id is None
    assert rows == []


@pytest.mark.parametrize(
    "manifest",
    [
        "",
        "   ",
        "slide_number,headline,subhead,image_ref,brand_template_id\n",  # header only
        "slide_number,headline\n1,no template column at all\n",
    ],
)
def test_an_unusable_manifest_yields_nothing_rather_than_a_partial_job(manifest):
    template_id, rows = dispatch._parse_canva_manifest(manifest)
    assert (template_id, rows) == (None, [])


# ---------------------------------------------------------------------
# The generation step
# ---------------------------------------------------------------------

def test_dry_run_is_the_default_and_constructs_no_client(monkeypatch, caplog):
    """canva-refresh-token is not populated, so a real call cannot work
    today. The default must therefore say so rather than fail weekly."""
    monkeypatch.delenv("CMOS_CANVA_DRY_RUN", raising=False)
    assert dispatch.canva_dry_run() is True

    def _explode():
        raise AssertionError("no mcp-canva client may be built in dry-run")

    monkeypatch.setattr(dispatch, "build_mcp_canva_client", _explode)

    with caplog.at_level(logging.INFO):
        dispatch._generate_carousel_designs(
            "task-1", {"canva_bulk_create_csv": MANIFEST}, db=None
        )

    assert any(r.message == "carousel_canva_dry_run" for r in caplog.records)


def test_live_mode_calls_bulk_create_from_csv_with_the_whole_deck(monkeypatch):
    monkeypatch.setenv("CMOS_CANVA_DRY_RUN", "false")
    calls: list = []
    monkeypatch.setattr(dispatch, "build_mcp_canva_client", lambda: _FakeCanvaClient(calls))

    dispatch._generate_carousel_designs("task-1", {"canva_bulk_create_csv": MANIFEST}, db=None)

    assert len(calls) == 1
    tool_name, arguments = calls[0]
    assert tool_name == "bulk_create_from_csv"
    assert arguments["template_id"] == "TPL-CAROUSEL-1"
    assert len(arguments["rows"]) == 3


def test_no_manifest_means_no_call_even_in_live_mode(monkeypatch):
    monkeypatch.setenv("CMOS_CANVA_DRY_RUN", "false")
    calls: list = []
    monkeypatch.setattr(dispatch, "build_mcp_canva_client", lambda: _FakeCanvaClient(calls))

    dispatch._generate_carousel_designs("task-1", {"canva_bulk_create_csv": ""}, db=None)
    dispatch._generate_carousel_designs("task-1", {}, db=None)

    assert calls == []


# ---------------------------------------------------------------------
# The property that matters most: this cannot break a draft
# ---------------------------------------------------------------------

def test_a_canva_outage_never_fails_the_drafting_task(monkeypatch, caplog):
    """The whole point of running this after set_result_ref.

    A dead-lettered Wednesday carousel because Canva was down would be a
    strictly worse system than the one that never called Canva -- which is
    what this replaces.
    """
    monkeypatch.setenv("CMOS_CANVA_DRY_RUN", "false")
    calls: list = []
    monkeypatch.setattr(
        dispatch,
        "build_mcp_canva_client",
        lambda: _FakeCanvaClient(calls, raises=MCPClientError("mcp-canva unreachable")),
    )

    transitions: list = []

    class _Db:
        def set_result_ref(self, *_a, **_kw):
            pass

        def transition(self, task_id, state, reason):
            transitions.append((task_id, state, reason))

        def advance_dependents(self, task_id):
            transitions.append(("advanced", task_id))

    # The hook is invoked exactly as the shared handler invokes it, and
    # the handler's own try/except is what must absorb this.
    db = _Db()
    with caplog.at_level(logging.WARNING):
        try:
            dispatch._generate_carousel_designs(
                "task-1", {"canva_bulk_create_csv": MANIFEST}, db
            )
        except MCPClientError:
            raised = True
        else:
            raised = False

    # _generate_carousel_designs itself does NOT swallow -- the shared
    # handler does, in one place, for every future hook. Pinning it here
    # so nobody "helpfully" adds a second swallow inside the step and
    # hides a real bug.
    assert raised is True
    assert calls, "the call was attempted"


def test_the_shared_handler_swallows_a_failing_post_draft_step():
    """The swallow lives in _draft_social_post_handler, once, for every
    hook -- read off the source rather than re-running a whole draft,
    which would need a gateway, a Vault and a database."""
    import inspect

    source = inspect.getsource(dispatch._draft_social_post_handler)
    assert "on_draft_complete(task_id, output, db)" in source
    assert "except MCPClientError as exc:" in source
    assert "draft_post_step_unreachable" in source
    assert "draft_post_step_failed" in source

    # It must run AFTER the result_ref is written and BEFORE the task is
    # completed -- enrichment on a draft that already exists.
    ref_at = source.index("db.set_result_ref(")
    hook_at = source.index("on_draft_complete(task_id, output, db)")
    done_at = source.index("db.transition(task_id, TaskStateEnum.COMPLETED")
    assert ref_at < hook_at < done_at


def test_the_carousel_handler_is_the_one_that_supplies_the_hook():
    import inspect

    source = inspect.getsource(dispatch.draft_carousel_post_handler)
    assert "on_draft_complete=_generate_carousel_designs" in source
