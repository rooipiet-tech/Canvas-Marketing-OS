"""Tests for the scan-profile registry (F-SCAN-PROFILE-SINGLETON).

functions/_shared/scan-profiles.yaml replaced function 09's own
fetch_sources.yaml, which described ONE scan (`topic` and `horizon_days`
were scalars at the root) for a system with eleven further scanner
packages already written. Those eleven had complete prompts, schemas,
tools and evals but nowhere to say what each of them scans, which is part
of why none was ever wired.

Eleven of the twelve profiles deliberately ship with no `urls`, because
nobody has yet written down where to read each sector. The behaviour that
matters is that such a profile is REFUSED loudly, never scanned empty --
so a scanner going live is an explicit act of filling in sources.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import yaml
from orchestrator import dispatch, worker
from orchestrator.config import functions_dir
from tests.fakes import patch_dispatch_clients
from tests.test_dispatch import FakeTaskDB, _envelope

PROFILES_PATH = functions_dir() / "_shared" / "scan-profiles.yaml"

# Every scanner package in the daily-signal-loop fan-out, plus function 09.
EXPECTED_FUNCTION_IDS = {
    "09-market-intelligence-director",
    "10-competitor-discovery-scanner",
    "11-competitor-change-monitor",
    "12-competitive-positioning-analyst",
    "13-competitor-content-performance-scout",
    "16-microsoft-fabric-ecosystem-scout",
    "18-01-vertical-intel-logistics-fleet",
    "18-02-vertical-intel-mining-industrial",
    "18-03-vertical-intel-manufacturing",
    "18-04-vertical-intel-construction",
    "18-05-vertical-intel-fmcg-beverage",
    "18-06-vertical-intel-financial-services",
}


def _document() -> dict[str, Any]:
    return yaml.safe_load(PROFILES_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------
# The file itself
# ---------------------------------------------------------------------


def test_every_scanner_package_has_a_profile():
    """A scanner with no profile has nowhere to say what it scans -- which
    is the state all eleven were in before this file existed."""
    document = _document()
    assert {profile["function_id"] for profile in document["profiles"]} == EXPECTED_FUNCTION_IDS


def test_every_profile_names_a_package_that_exists_on_disk():
    """Catches a rename on either side -- the 18-04 construction rename
    would have left a dangling function_id here."""
    for profile in _document()["profiles"]:
        assert (functions_dir() / profile["function_id"]).is_dir(), profile["profile_id"]


def test_profile_ids_are_unique_and_every_profile_carries_a_real_topic():
    profiles = _document()["profiles"]
    ids = [profile["profile_id"] for profile in profiles]

    assert len(set(ids)) == len(ids)
    for profile in profiles:
        # A profile with no topic is not awaiting sources, it is unfinished.
        assert len(profile["topic"]) > 20, profile["profile_id"]


def test_defaults_cover_every_knob_a_profile_may_omit():
    defaults = _document()["defaults"]

    assert set(defaults) == {
        "horizon_days",
        "min_sources",
        "min_distinct_domains",
        "source_chars",
    }


# ---------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------


def test_defaults_are_merged_under_a_profiles_own_keys():
    resolved = dispatch._resolve_scan_profile("market-intelligence")

    assert resolved["horizon_days"] == 30
    assert resolved["min_sources"] == 2
    assert resolved["min_distinct_domains"] == 2
    assert resolved["source_chars"] == 8000
    assert resolved["topic"].startswith("Microsoft Fabric adoption")
    assert len(resolved["urls"]) == 4


def test_a_sourceless_profile_is_refused_by_name_not_scanned_empty():
    """The eleven unwired scanners must fail in a way that says exactly
    what to do about it."""
    with pytest.raises(dispatch.DispatchError) as exc:
        dispatch._resolve_scan_profile("vertical-construction")

    message = str(exc.value)
    assert "vertical-construction" in message
    assert "no source urls" in message
    assert "scan-profiles.yaml" in message


@pytest.mark.parametrize(
    "profile_id",
    sorted(
        profile["profile_id"]
        for profile in yaml.safe_load(PROFILES_PATH.read_text(encoding="utf-8"))["profiles"]
        if not profile["urls"]
    ),
)
def test_every_sourceless_profile_refuses(profile_id):
    with pytest.raises(dispatch.DispatchError, match="no source urls"):
        dispatch._resolve_scan_profile(profile_id)


def test_an_unknown_profile_id_names_the_ones_that_exist():
    """A typo in a loop YAML's params must not silently fall back to
    scanning the wrong market."""
    with pytest.raises(dispatch.DispatchError) as exc:
        dispatch._resolve_scan_profile("vertical-construction-buildsmart")

    assert "is not defined" in str(exc.value)
    assert "market-intelligence" in str(exc.value)


# ---------------------------------------------------------------------
# How a profile_id reaches the handler
# ---------------------------------------------------------------------


def test_loop_params_carry_profile_id_onto_the_envelope():
    assert worker._task_metadata({"profile_id": "vertical-mining-industrial"}) == {
        "profile_id": "vertical-mining-industrial"
    }


def test_proof_circuit_and_profile_id_coexist():
    metadata = worker._task_metadata({"proof_circuit": True, "profile_id": "market-intelligence"})

    assert metadata == {"proof_circuit": "true", "profile_id": "market-intelligence"}


def test_params_without_either_key_still_carry_no_metadata():
    """_task_metadata is still not a general params-bag passthrough."""
    assert worker._task_metadata({"function_id": "publish.social_post"}) is None
    assert worker._task_metadata(None) is None


def test_the_daily_loop_names_its_profile_explicitly():
    loop_path = (
        functions_dir().parent / "services" / "orchestrator" / "loops" / "daily-signal-loop.yaml"
    )
    loop = yaml.safe_load(loop_path.read_text(encoding="utf-8"))
    ingest = next(task for task in loop["tasks"] if task["task_id"] == "ingest")

    assert ingest["params"]["profile_id"] == "market-intelligence"


def test_handler_defaults_to_market_intelligence_when_no_param_is_set():
    envelope = _envelope(str(uuid.uuid4()), "ingest-signals")

    assert dispatch._envelope_scan_profile_id(envelope) == dispatch.DEFAULT_SCAN_PROFILE_ID


def test_handler_reads_the_profile_id_off_the_envelope(monkeypatch):
    envelope = _envelope(str(uuid.uuid4()), "ingest-signals")
    envelope.metadata = {"profile_id": "vertical-financial-services"}

    assert dispatch._envelope_scan_profile_id(envelope) == "vertical-financial-services"


@pytest.fixture()
def clients(monkeypatch):
    return patch_dispatch_clients(monkeypatch)


def test_an_unwired_scanners_profile_fails_the_task_rather_than_completing(clients, monkeypatch):
    """End to end: a loop task pointed at a sourceless profile dead-ends
    loudly instead of writing an empty signals row."""
    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    db.seed(task_id, "ingest-signals")
    envelope = _envelope(task_id, "ingest-signals")
    envelope.metadata = {"profile_id": "vertical-fmcg-beverage"}

    with pytest.raises(dispatch.DispatchError, match="no source urls"):
        dispatch.ingest_signals_handler(task_id, envelope, db)

    assert db.get_task(task_id)["state"] != "completed"
    assert not clients._signals


def test_a_completed_scan_records_which_profile_it_scanned(clients, monkeypatch):
    db = FakeTaskDB()
    task_id = str(uuid.uuid4())
    db.seed(task_id, "ingest-signals")

    dispatch.ingest_signals_handler(task_id, _envelope(task_id, "ingest-signals"), db)

    assert db.get_result_ref(task_id)["scan_profile_id"] == "market-intelligence"
